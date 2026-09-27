import copy
import hashlib
import json
import unittest
from pathlib import Path
from validate_quant_theme_review import validate, render

ROOT=Path(__file__).resolve().parents[2]
SCHEMA=json.loads((ROOT/'contracts/key10_review_quant_theme.schema.json').read_text())

def fixture():
    rows=[]
    for i,code in enumerate(['600001.SH','000001.SZ','688001.SH'],1):
        rows.append(dict(ts_code=code,name='合成测试'+str(i),origin='official_pool',
                         selection_order=i,quant_score=30.,quant_rank=i,technical_score=20.,
                         trade_date='20260924',data_complete=True))
    b=dict(schema_version='2.0.0',model_version='V2.0',stage='21:30_quantitative_review_payload',
           trade_date='20260924',next_trade_date='20260928',future_data_rows_used=0,
           official_review_candidates=rows,external_review_candidates=[])
    raw=json.dumps(b).encode()
    output=[]
    for i,src in enumerate(rows):
        r={k:src[k] for k in ['ts_code','name','origin','selection_order','quant_score','quant_rank','technical_score']}
        r.update(input_snapshot=copy.deepcopy(src),selection_status=['selected','watch','deferred'][i],
                 final_order=1 if i==0 else None,reason='仅用于契约测试',risk_notes=[],next_day_action='仅观察',reorder=None)
        output.append(r)
    r=dict(review_contract_version='quant_theme_rotation_20260927',schema_version='2.0.0',model_version='V2.0',
           stage='final_review',input_stage=b['stage'],trade_date='20260924',next_trade_date='20260928',
           evidence_cutoff='2026-09-24T18:40:00+08:00',future_data_rows_used=0,
           input_sha256=hashlib.sha256(raw).hexdigest(),attempt_no=0,
           market_context=dict(reported_facts=[],analysis_inferences=[],missing=['题材背景缺失'],decision='测试：一只短名单，一只观察，一只暂缓'),
           official_candidates=output,external_candidates=[])
    return raw,r

class ContractTests(unittest.TestCase):
    def ok(self,raw,r): return validate(raw,r,SCHEMA,'20260924')
    def test_valid_missing_research_all_statuses(self):
        raw,r=fixture(); self.assertTrue(self.ok(raw,r)); self.assertIn('题材背景缺失',render(r))
    def test_bad_cases(self):
        mutations=[lambda r:r.update(final_score=90),
                   lambda r:r.update(trade_date='20260923'),
                   lambda r:r.update(input_sha256='0'*64),
                   lambda r:r.update(future_data_rows_used=1),
                   lambda r:r['official_candidates'][1].update(final_order=2),
                   lambda r:r['official_candidates'][0]['input_snapshot'].update(quant_score=99),
                   lambda r:r['official_candidates'][0].update(technical_score=17),
                   lambda r:r['official_candidates'].pop(),
                   lambda r:r.update(evidence_cutoff='2026-09-24T18:40:00'),
                   lambda r:r['market_context']['reported_facts'].append(dict(summary='未来',source=dict(url='https://example.com',published_at='2026-09-25T00:00:00+08:00')))]
        for mutate in mutations:
            raw,r=fixture(); mutate(r)
            with self.subTest(change=mutations.index(mutate)),self.assertRaises((ValueError,TypeError)):
                self.ok(raw,r)
    def test_reorder(self):
        raw,r=fixture()
        for i,c in enumerate(r['official_candidates'][:2]): c.update(selection_status='selected',final_order=2-i)
        with self.assertRaises(ValueError): self.ok(raw,r)
        for c in r['official_candidates'][:2]:
            c['reorder']=dict(from_selection_order=c['selection_order'],to_final_order=c['final_order'],reason='合成改序',
                              sources=[dict(url='https://example.com/test',published_at='2026-09-23')])
        self.assertTrue(self.ok(raw,r))
    def test_upstream_missing_sequence(self):
        raw,r=fixture(); b=json.loads(raw); del b['official_review_candidates'][0]['selection_order']
        raw=json.dumps(b).encode(); r['input_sha256']=hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ValueError): self.ok(raw,r)
    def test_report_correspondence(self):
        raw,r=fixture(); before=render(r); r['official_candidates'][0]['reason']='更改理由'
        self.assertNotEqual(before,render(r))

if __name__=='__main__': unittest.main()
