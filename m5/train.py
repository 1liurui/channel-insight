"""YOLOv8s 在 SKU110K 上微调。

从 COCO 预训练权重出发做单类别微调，不是从零训。几个和默认值不同的地方：

- `max_det` 默认 300，而 SKU110K 平均每图约 147 个框、最密的超过 400 个。
  验证阶段一旦被 300 截断，mAP 和后面的计数误差都会被系统性低估——
  这是这个数据集上最容易踩的坑，训练/验证/推理三处都要一致地放开。
- `close_mosaic`：最后若干 epoch 关掉 mosaic 拼图增强。密集小目标场景下
  mosaic 前期涨点明显，但收尾阶段留着会让框回归学不干净。
- `cache=False`：预缩放后单图很小，但 8k 张仍有 ~2GB，交给页缓存比进程内缓存稳。
"""
from __future__ import annotations

import argparse

from ultralytics import YOLO


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yolov8s.pt")
    ap.add_argument("--data", default="/root/autodl-tmp/m5/sku110k.yaml")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--name", default="sku110k")
    ap.add_argument("--fraction", type=float, default=1.0, help="只用前 N 比例的训练集，冒烟测试用")
    args = ap.parse_args()

    YOLO(args.model).train(
        data=args.data,
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        workers=args.workers,
        fraction=args.fraction,
        project="/root/autodl-tmp/runs",
        name=args.name,
        exist_ok=True,
        # —— 密集小目标的关键设置 ——
        max_det=1000,
        close_mosaic=10,
        patience=15,
        # —— 增强：货架图always正立，不做旋转/上下翻转 ——
        degrees=0.0,
        flipud=0.0,
        fliplr=0.5,
        scale=0.5,
        cache=False,
        plots=True,
        seed=0,
    )


if __name__ == "__main__":
    main()
