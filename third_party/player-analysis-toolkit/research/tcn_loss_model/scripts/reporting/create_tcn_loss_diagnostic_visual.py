#!/usr/bin/env python3
"""Create an inline diagnostic scatter/calibration visual from node predictions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=6000)
    return parser.parse_args()


def calibration(frame: pd.DataFrame, probability: str, actual: str) -> list[dict]:
    values = frame[[probability, actual]].dropna().copy()
    values["bin"] = np.minimum((values[probability] * 10).astype(int), 9)
    grouped = values.groupby("bin", observed=True).agg(
        predicted=(probability, "mean"), observed=(actual, "mean"), count=(actual, "size")
    )
    return [
        {
            "predicted": round(float(row.predicted), 5),
            "observed": round(float(row.observed), 5),
            "count": int(row.count),
        }
        for row in grouped.itertuples()
    ]


def main() -> int:
    args = parse_args()
    frame = pd.read_csv(args.predictions, low_memory=False, encoding="utf-8")
    test = frame.loc[frame["split"].eq("test")].copy()
    if test.empty:
        raise ValueError("predictions contain no test rows")
    sample = test.sample(n=min(args.sample_size, len(test)), random_state=42).copy()
    rng = np.random.default_rng(42)
    loss = pd.to_numeric(sample["actual_disc_loss"], errors="raise").to_numpy(float)
    classes = np.select([loss == 0, loss <= 3, loss <= 9], [0, 1, 2], default=3)
    scatter = [
        [
            round(float(min(value, 20) + rng.uniform(-0.26, 0.26)), 3),
            round(float(probability), 5),
            int(category),
            int(ply),
            int(value),
        ]
        for value, probability, category, ply in zip(
            loss,
            sample["probability_loss_ge4"].to_numpy(float),
            classes,
            sample["global_placement_ply"].to_numpy(int),
        )
    ]
    trend_frame = test.assign(loss_capped=np.minimum(test["actual_disc_loss"].astype(int), 20))
    trend_summary = trend_frame.groupby("loss_capped").agg(
        mean_probability=("probability_loss_ge4", "mean"), node_count=("probability_loss_ge4", "size")
    ).reset_index()
    trend = [
        [int(row.loss_capped), round(float(row.mean_probability), 5), int(row.node_count)]
        for row in trend_summary.itertuples(index=False)
    ]
    payload = {
        "scatter": scatter,
        "trend": trend,
        "calibration": {
            "零子损": calibration(test, "probability_loss_zero", "actual_loss_zero"),
            "子损≥4": calibration(test, "probability_loss_ge4", "actual_loss_ge4"),
            "子损≥10": calibration(test, "probability_loss_ge10", "actual_loss_ge10"),
        },
        "rows": int(len(test)),
        "sample": int(len(sample)),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fragment = f'''<div id="tcn-loss-diagnostic-v1">
  <style>
    #tcn-loss-diagnostic-v1 {{ color: var(--foreground); width: 100%; }}
    #tcn-loss-diagnostic-v1 .plot-grid {{ display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(260px, .75fr); gap: 18px; align-items: start; }}
    #tcn-loss-diagnostic-v1 .plot-section {{ min-width: 0; }}
    #tcn-loss-diagnostic-v1 .plot-label {{ margin: 0 0 6px; font-weight: 500; }}
    #tcn-loss-diagnostic-v1 .plot-note {{ margin: 6px 0 0; color: var(--muted-foreground); }}
    #tcn-loss-diagnostic-v1 canvas {{ display: block; width: 100%; background: transparent; }}
    #tcn-loss-diagnostic-v1 .cal-stack {{ display: grid; gap: 12px; }}
    #tcn-loss-diagnostic-v1 .legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 6px; color: var(--muted-foreground); }}
    #tcn-loss-diagnostic-v1 .legend-item {{ display: inline-flex; gap: 5px; align-items: center; }}
    #tcn-loss-diagnostic-v1 .swatch {{ width: 9px; height: 9px; border-radius: 50%; background: var(--swatch); }}
    #tcn-loss-diagnostic-v1 .hover-detail {{ min-height: 1.5em; margin-top: 4px; color: var(--muted-foreground); }}
    @media (max-width: 680px) {{ #tcn-loss-diagnostic-v1 .plot-grid {{ grid-template-columns: 1fr; }} }}
  </style>
  <div class="plot-grid">
    <section class="plot-section" aria-labelledby="loss-scatter-label">
      <div class="plot-label" id="loss-scatter-label">实际子损 vs 模型预测的“子损≥4”概率</div>
      <canvas id="loss-scatter" height="430" role="img" aria-label="测试集节点散点图；横轴为实际子损，纵轴为模型预测的子损至少四概率"></canvas>
      <div class="legend text-small" aria-label="实际子损类别图例">
        <span class="legend-item"><span class="swatch" style="--swatch:var(--viz-series-1)"></span>0</span>
        <span class="legend-item"><span class="swatch" style="--swatch:var(--viz-series-2)"></span>1–3</span>
        <span class="legend-item"><span class="swatch" style="--swatch:var(--viz-series-3)"></span>4–9</span>
        <span class="legend-item"><span class="swatch" style="--swatch:var(--viz-series-4)"></span>≥10</span>
        <span class="legend-item">实线：每个实际子损的平均预测</span>
      </div>
      <div class="hover-detail text-small" id="scatter-detail">悬停查看节点；20 表示 20 子及以上。</div>
    </section>
    <section class="plot-section" aria-labelledby="calibration-label">
      <div class="plot-label" id="calibration-label">概率校准</div>
      <div class="cal-stack" id="calibration-stack"></div>
      <p class="plot-note text-small">虚线是理想校准；圆越大，该概率区间的节点越多。</p>
    </section>
  </div>
  <script>
    (() => {{
      const root = document.getElementById('tcn-loss-diagnostic-v1');
      const DATA = {data};
      const colors = [...root.querySelectorAll('.swatch')].map(node => getComputedStyle(node).backgroundColor);
      const fg = getComputedStyle(root.querySelector('.plot-label')).color;
      const muted = getComputedStyle(root.querySelector('.plot-note')).color;
      const border = muted;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);

      function setup(canvas, height) {{
        const width = Math.max(280, canvas.clientWidth);
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
        const ctx = canvas.getContext('2d');
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        return {{ctx, width, height}};
      }}
      function line(ctx, x1, y1, x2, y2, color, dash=[], alpha=1) {{
        ctx.save(); ctx.globalAlpha=alpha;
        ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.strokeStyle=color;
        ctx.lineWidth=1; ctx.setLineDash(dash); ctx.stroke(); ctx.restore();
      }}
      function text(ctx, value, x, y, align='center') {{
        ctx.fillStyle=muted; ctx.font='12px system-ui'; ctx.textAlign=align; ctx.fillText(value,x,y);
      }}
      function drawScatter() {{
        const canvas=root.querySelector('#loss-scatter');
        const {{ctx,width,height}}=setup(canvas, 430);
        const m={{l:48,r:14,t:12,b:38}}, w=width-m.l-m.r, h=height-m.t-m.b;
        const sx=x=>m.l+(Math.max(0,Math.min(20,x))/20)*w;
        const sy=y=>m.t+(1-Math.max(0,Math.min(1,y)))*h;
        ctx.clearRect(0,0,width,height);
        for(let v=0;v<=1.001;v+=.2){{ const y=sy(v); line(ctx,m.l,y,width-m.r,y,border,[],.22); text(ctx,v.toFixed(1),m.l-7,y+4,'right'); }}
        [0,4,8,12,16,20].forEach(v=>{{ const x=sx(v); line(ctx,x,m.t,x,height-m.b,border,[],.22); text(ctx,v===20?'20+':String(v),x,height-12); }});
        text(ctx,'实际子损',m.l+w/2,height-1);
        ctx.save(); ctx.translate(13,m.t+h/2); ctx.rotate(-Math.PI/2); text(ctx,'预测 P(子损≥4)',0,0); ctx.restore();
        DATA.scatter.forEach(p=>{{ ctx.beginPath(); ctx.arc(sx(p[0]),sy(p[1]),2,0,Math.PI*2); ctx.fillStyle=colors[p[2]]; ctx.globalAlpha=.18; ctx.fill(); }});
        ctx.globalAlpha=1; ctx.beginPath(); DATA.trend.forEach((p,i)=>{{ const x=sx(p[0]),y=sy(p[1]); if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y); }}); ctx.strokeStyle=fg; ctx.lineWidth=2; ctx.stroke();
        canvas.onmousemove=e=>{{
          const rect=canvas.getBoundingClientRect(), px=(e.clientX-rect.left)*width/rect.width, py=(e.clientY-rect.top)*height/rect.height;
          let best=null,dist=64; for(const p of DATA.scatter){{const dx=sx(p[0])-px,dy=sy(p[1])-py,d=dx*dx+dy*dy;if(d<dist){{dist=d;best=p;}}}}
          root.querySelector('#scatter-detail').textContent=best ? `实际子损 ${{best[4]}}；预测≥4概率 ${{(best[1]*100).toFixed(1)}}%；ply ${{best[3]}}` : '悬停查看节点；20 表示 20 子及以上。';
        }};
      }}
      function drawCalibration(canvas, points, color) {{
        const {{ctx,width,height}}=setup(canvas, 122);
        const m={{l:34,r:10,t:6,b:26}}, w=width-m.l-m.r, h=height-m.t-m.b;
        const sx=x=>m.l+x*w, sy=y=>m.t+(1-y)*h;
        ctx.clearRect(0,0,width,height); line(ctx,sx(0),sy(0),sx(1),sy(1),fg,[4,4],.7);
        [0,.5,1].forEach(v=>{{text(ctx,v.toFixed(1),sx(v),height-7); text(ctx,v.toFixed(1),m.l-5,sy(v)+4,'right');}});
        ctx.beginPath(); points.forEach((p,i)=>{{const x=sx(p.predicted),y=sy(p.observed);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}});ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();
        const max=Math.max(...points.map(p=>p.count)); points.forEach(p=>{{ctx.beginPath();ctx.arc(sx(p.predicted),sy(p.observed),3+5*Math.sqrt(p.count/max),0,Math.PI*2);ctx.fillStyle=color;ctx.globalAlpha=.75;ctx.fill();}});ctx.globalAlpha=1;
      }}
      function buildCalibration() {{
        const stack=root.querySelector('#calibration-stack'); stack.replaceChildren();
        Object.entries(DATA.calibration).forEach(([label,points],i)=>{{
          const wrap=document.createElement('div'); const caption=document.createElement('div'); caption.className='text-small'; caption.textContent=label;
          const canvas=document.createElement('canvas'); canvas.height=122; canvas.setAttribute('role','img'); canvas.setAttribute('aria-label',`${{label}}概率校准图，横轴预测概率，纵轴实际发生率`);
          wrap.append(caption,canvas); stack.append(wrap); drawCalibration(canvas,points,colors[i+1]);
        }});
      }}
      function render() {{ drawScatter(); buildCalibration(); }}
      render(); new ResizeObserver(render).observe(root);
    }})();
  </script>
</div>
'''
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(fragment, encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output.resolve()), **{key: payload[key] for key in ("rows", "sample")}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
