"""快速健康检查：一组典型问题跑通链路，看成功率、一次通过率与延迟。"""

from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.service import AskService

QS = [
 "华东区上个月的销售额是多少",
 "各渠道的净销额排名",
 "清源品牌在便利店渠道卖了多少支",
 "2026年8月哪个大区的费效比最高",
 "临期库存占比最高的前十个经销商",
 "各大区业务代表的拜访达成率",
 "上个月碳酸饮料品类的动销率是多少",
 "对比华东和华南上个月的单店产出",
]
svc = AskService(use_cache=False)
ok=first=0; lat=[]
for q in QS:
    a = svc.ask(q); lat.append(a.latency_ms)
    if a.ok: ok+=1
    if a.ok and a.retry==0: first+=1
    status = "OK " if a.ok else "FAIL"
    note = f"纠错{a.retry}轮" if a.retry else "一次过"
    err = f"  ← {a.attempts[0]['error'][:70]}" if a.attempts else ""
    val = str(a.rows[0])[:60] if a.rows else "(空结果)"
    print(f"{status} {a.latency_ms:>6.0f}ms {note:<8} {q:<28} {val}{err}")
lat.sort()
print(f"\n最终成功 {ok}/{len(QS)}   一次通过 {first}/{len(QS)}   "
      f"延迟中位 {lat[len(lat)//2]:.0f}ms  最大 {lat[-1]:.0f}ms")
