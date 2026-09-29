#!/usr/bin/env python3
"""Validate the bundled schema subset plus input-bound semantics; no network/writes unless --write-text."""
import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

CN = timezone(timedelta(hours=8))

def require(ok, message):
    if not ok:
        raise ValueError(message)

def schema_check(v, s, path='$'):
    # All validation keywords used by the accompanying schema are implemented.
    supported = {'$schema','title','type','const','enum','required','properties',
                 'additionalProperties','items','minItems','maxItems','minimum','minLength','pattern'}
    require(not set(s) - supported, f'{path}: unsupported schema keyword')
    types = {'object': lambda x: type(x) is dict, 'array': lambda x: type(x) is list,
             'string': lambda x: type(x) is str, 'integer': lambda x: type(x) is int,
             'number': lambda x: type(x) in (int,float) and math.isfinite(x),
             'null': lambda x: x is None}
    if 'type' in s:
        ts = s['type'] if isinstance(s['type'], list) else [s['type']]
        require(any(types[t](v) for t in ts), f'{path}: type')
    if 'const' in s:
        require(v == s['const'], f'{path}: const')
    if 'enum' in s:
        require(v in s['enum'], f'{path}: enum')
    if isinstance(v, dict):
        require(set(s.get('required', [])) <= v.keys(), f'{path}: required')
        props = s.get('properties', {})
        if s.get('additionalProperties') is False:
            require(not v.keys() - props.keys(), f'{path}: unexpected fields')
        for k in v.keys() & props.keys():
            schema_check(v[k], props[k], path+'.'+k)
    if isinstance(v, list):
        require(len(v) >= s.get('minItems', 0), f'{path}: minItems')
        require(len(v) <= s.get('maxItems', float('inf')), f'{path}: maxItems')
        for i,x in enumerate(v):
            if 'items' in s:
                schema_check(x, s['items'], f'{path}[{i}]')
    if type(v) in (int,float):
        require(math.isfinite(v) and v >= s.get('minimum', -float('inf')), f'{path}: number')
    if isinstance(v, str):
        require(len(v) >= s.get('minLength', 0), f'{path}: minLength')
        if 'pattern' in s:
            require(re.search(s['pattern'], v) is not None, f'{path}: pattern')

def timestamp(s):
    t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    require(t.tzinfo is not None, 'timestamp needs timezone')
    return t

def source_check(source, cutoff):
    s = source['published_at']
    if len(s) == 10:
        d = datetime.strptime(s, '%Y-%m-%d').date()
        # Day-only publication on cutoff day cannot establish intraday availability.
        require(d < cutoff.astimezone(CN).date(), 'same-day source requires publication time')
    else:
        require(timestamp(s) <= cutoff, 'future source')

def future_counts(obj):
    values = []
    if isinstance(obj, dict):
        for k,v in obj.items():
            if k == 'future_data_rows_used':
                values.append(v)
            else:
                values.extend(future_counts(v))
    elif isinstance(obj, list):
        for v in obj:
            values.extend(future_counts(v))
    return values

def validate(bundle_raw, review, schema, expected_date):
    schema_check(review, schema)
    b = json.loads(bundle_raw)
    datetime.strptime(expected_date, '%Y%m%d')
    require(b['trade_date'] == review['trade_date'] == expected_date, 'trade date mismatch')
    require(b['schema_version'] == review['schema_version'] == '2.0.0', 'schema mismatch')
    require(b['model_version'] == review['model_version'] == 'V2.0', 'model mismatch')
    require(b['stage'] == review['input_stage'] == '21:30_quantitative_review_payload', 'input stage')
    require(b['next_trade_date'] == review['next_trade_date'] > expected_date, 'next trade date')
    require(hashlib.sha256(bundle_raw).hexdigest() == review['input_sha256'], 'raw input SHA256')
    guards = future_counts(b)
    require(guards and all(type(v) is int and v == 0 for v in guards), 'future guard missing/nonzero')
    cutoff = timestamp(review['evidence_cutoff'])
    require(cutoff.astimezone(CN).strftime('%Y%m%d') == expected_date, 'cutoff trade date')
    require(cutoff <= datetime.now(timezone.utc), 'cutoff in future')
    if b.get('generated_at'):
        require(timestamp(b['generated_at']) <= cutoff, 'bundle generated after evidence cutoff')
    for f in review['market_context']['reported_facts']:
        source_check(f['source'], cutoff)
    seen = set()
    for key, input_key, origin, capacity, limit in [
        ('official_candidates','official_review_candidates','official_pool',30,10),
        ('external_candidates','external_review_candidates','external_pool',10,3)]:
        inputs, outputs = b[input_key], review[key]
        require(len(inputs) <= capacity and len(outputs) == len(inputs), 'pool coverage/size')
        require([r.get('selection_order') for r in inputs] == list(range(1,len(inputs)+1)), 'upstream selection_order')
        require([r['ts_code'] for r in inputs] == [r['ts_code'] for r in outputs], 'decision coverage/order')
        for src,r in zip(inputs,outputs):
            code = src['ts_code']
            require(code not in seen, 'duplicate code/cross-pool overlap')
            seen.add(code)
            require(re.fullmatch(r'(?:60\d{4}|688\d{3})\.SH|(?:00\d{4}|30\d{4})\.SZ', code), 'market range')
            require(src.get('trade_date') == expected_date and src.get('origin') == origin, 'candidate date/origin')
            require(src.get('data_complete') is True, 'incomplete upstream')
            require(type(src.get('technical_score')) in (int,float) and math.isfinite(src['technical_score']) and src['technical_score'] >= 18, 'technical gate')
            require(r['input_snapshot'] == src, 'input snapshot altered')
            for k in ('ts_code','name','origin','selection_order','quant_score','quant_rank','technical_score'):
                require(r[k] == src[k], 'input field altered: '+k)
            require('ST' not in src['name'].upper() and '退' not in src['name'], 'risk name: ST/delisting')
            if src.get('kdj_as_of'):
                require(src['kdj_as_of'] <= expected_date, 'future KDJ')
            if src.get('kdj_data_complete') is True:
                k,d,j = (src.get('kdj_'+x) for x in ('k','d','j'))
                require(all(type(x) in (int,float) and math.isfinite(x) for x in (k,d,j)), 'KDJ finite')
                require(0 <= k <= 100 and 0 <= d <= 100 and abs(j-(3*k-2*d)) < 1e-5, 'KDJ range/formula')
            if r['selection_status'] != 'selected':
                require(r['final_order'] is None and r['reorder'] is None, 'watch/deferred rank')
            if r['reorder'] is not None:
                adj = r['reorder']
                require(adj['from_selection_order'] == r['selection_order'] and adj['to_final_order'] == r['final_order'], 'reorder positions')
                for source in adj['sources']:
                    source_check(source, cutoff)
        chosen = [r for r in outputs if r['selection_status']=='selected']
        require(len(chosen) <= limit, 'selected pool limit')
        require(sorted(r['final_order'] for r in chosen) == list(range(1,len(chosen)+1)), 'selected ranks not continuous')
        ordered = sorted(chosen, key=lambda r:r['final_order'])
        if [r['selection_order'] for r in ordered] != sorted(r['selection_order'] for r in chosen):
            require(all(r['reorder'] is not None for r in chosen), 'changed order needs complete adjustment record')
    return True

def render(r):
    lines = ['# V2.0每日选股', '', f"交易日：{r['trade_date']}；下一交易日：{r['next_trade_date']}",
             f"证据截止：{r['evidence_cutoff']}", '', '## 市场与题材', '', '报道事实：']
    ctx = r['market_context']
    for f in ctx['reported_facts']:
        lines.append(f"- {f['summary']}（{f['source']['published_at']}，{f['source']['url']}）")
    if not ctx['reported_facts']:
        lines.append('- 未取得可核验报道。')
    lines += ['', '分析推断：'] + ['- '+x for x in ctx['analysis_inferences']]
    lines += ['', '资料缺失：'] + ['- '+x for x in ctx['missing']]
    lines += ['', '本次选择判断：'+ctx['decision']]
    for key,title in [('official_candidates','池内候选'),('external_candidates','池外独立观察')]:
        lines += ['', '## '+title, '']
        rows = sorted(r[key], key=lambda x:(x['selection_status']!='selected', x['final_order'] or x['selection_order']))
        for c in rows:
            status = {'selected':'短名单','watch':'观察','deferred':'暂缓'}[c['selection_status']]
            theme = c['input_snapshot'].get('theme_classification') or {}
            lines += [f"- {c['name']}（{c['ts_code']}）：{status}；正式序号 {c['final_order']}；上游顺序 {c['selection_order']}；原量化分 {c['quant_score']}／排名 {c['quant_rank']}。",
                      '  题材：'+str(theme.get('primary_theme') or '待分类')+'；路线：'+str(c['input_snapshot'].get('candidate_source',{}).get('routes',[])),
                      '  依据：'+c['reason'], '  风险：'+('；'.join(c['risk_notes']) or '未另列风险，不代表无风险'),
                      '  我的判断：'+c['next_day_action']]
            if c['reorder']:
                lines.append('  改序依据：'+c['reorder']['reason'])
                lines += ['  来源：'+s['url']+'（'+s['published_at']+'）' for s in c['reorder']['sources']]
    return '\n'.join(lines)+'\n'

def main():
    ap=argparse.ArgumentParser()
    for name in ('schema','bundle','review','trade-date'):
        ap.add_argument('--'+name, required=True)
    group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--write-text'); group.add_argument('--text')
    a=ap.parse_args()
    r=json.loads(Path(a.review).read_text(encoding='utf-8'))
    validate(Path(a.bundle).read_bytes(),r,json.loads(Path(a.schema).read_text(encoding='utf-8')),a.trade_date)
    output=render(r)
    if a.write_text:
        with open(a.write_text,'x',encoding='utf-8',newline='\n') as f:
            f.write(output)
    else:
        require(Path(a.text).read_bytes()==output.encode('utf-8'), 'text differs from JSON rendering')
    print('VALIDATION_PASSED: schema, input binding, pools, order, statuses, text')

if __name__=='__main__':
    main()
