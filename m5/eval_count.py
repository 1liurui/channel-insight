"""排面计数误差评测。

简历里报的数字是**计数误差**而不是 mAP，理由有两个：一是对数字化中心的读者，
「一张货架图数错几个商品」比 mAP 直观；二是 SKU110K 上 AP@50 通行值 >0.88、
AP@[.5:.95] 才是 0.5 量级，两个口径混报必被追问。计数误差没有这个歧义。

定义：每张图 `|预测框数 − 真值框数| / 真值框数`，对全测试集取平均（MAPE）。
同时报中位数——计数误差分布右偏，少数极端遮挡图会把均值拽高。

**阈值必须在 val 上选、在 test 上报。** 计数对 conf 阈值极其敏感
（阈值低则重框、高则漏检），拿测试集去挑阈值等于把测试集当验证集用，
报出来的数字不能信。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from ultralytics import YOLO

MAX_DET = 1000  # 必须与训练一致；默认 300 会把密集图截断


def gt_counts(split_dir: Path) -> dict[str, int]:
    return {p.stem: sum(1 for _ in p.open()) for p in sorted(split_dir.glob("*.txt"))}


def predict_counts(model: YOLO, img_dir: Path, conf: float, imgsz: int, batch: int) -> dict[str, int]:
    out: dict[str, int] = {}
    imgs = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    for i in range(0, len(imgs), batch):
        chunk = imgs[i : i + batch]
        for p, r in zip(chunk, model.predict(chunk, conf=conf, imgsz=imgsz, max_det=MAX_DET, verbose=False)):
            out[p.stem] = len(r.boxes)
        if (i // batch) % 20 == 0:
            print(f"  {i + len(chunk)}/{len(imgs)}", flush=True)
    return out


def score(pred: dict[str, int], gt: dict[str, int]) -> dict[str, float]:
    keys = [k for k in gt if k in pred and gt[k] > 0]
    err = np.array([abs(pred[k] - gt[k]) / gt[k] for k in keys])
    bias = np.array([(pred[k] - gt[k]) / gt[k] for k in keys])
    return {
        "images": len(keys),
        "count_error_mean": float(err.mean()),
        "count_error_median": float(np.median(err)),
        "bias_mean": float(bias.mean()),  # 正=系统性多数，负=系统性漏数
        "within_10pct": float((err <= 0.10).mean()),
        "gt_boxes_per_image": float(np.mean([gt[k] for k in keys])),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--root", default="/root/autodl-tmp/sku110k")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--confs", default="0.15,0.20,0.25,0.30,0.35,0.40,0.45,0.50")
    ap.add_argument("--out", default="/root/autodl-tmp/m5/count_report.json")
    args = ap.parse_args()

    root = Path(args.root)
    model = YOLO(args.weights)

    # 第一步：在 val 上扫阈值
    val_gt = gt_counts(root / "labels" / "val")
    sweep = {}
    for conf in [float(c) for c in args.confs.split(",")]:
        print(f"[val] conf={conf}")
        sweep[conf] = score(predict_counts(model, root / "images" / "val", conf, args.imgsz, args.batch), val_gt)
        print(f"       计数误差均值 {sweep[conf]['count_error_mean']:.4f}")

    best = min(sweep, key=lambda c: sweep[c]["count_error_mean"])
    print(f"\n>>> val 选定阈值 conf={best}（计数误差 {sweep[best]['count_error_mean']:.4f}）\n")

    # 第二步：用选定阈值在 test 上报数
    test_gt = gt_counts(root / "labels" / "test")
    print(f"[test] conf={best}")
    test = score(predict_counts(model, root / "images" / "test", best, args.imgsz, args.batch), test_gt)

    report = {"best_conf": best, "val_sweep": {str(k): v for k, v in sweep.items()}, "test": test}
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print("\n================ 测试集结果 ================")
    print(f"图片数            {test['images']}")
    print(f"真值均值          {test['gt_boxes_per_image']:.1f} 框/图")
    print(f"排面计数误差(均值) {test['count_error_mean'] * 100:.2f}%   <<< 简历数字 ⑥")
    print(f"排面计数误差(中位) {test['count_error_median'] * 100:.2f}%")
    print(f"偏差(正=多数)      {test['bias_mean'] * 100:+.2f}%")
    print(f"误差≤10% 的图占比  {test['within_10pct'] * 100:.1f}%")
    print(f"\n报告已写入 {args.out}")


if __name__ == "__main__":
    main()
