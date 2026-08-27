import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  GridComponent, TooltipComponent, LegendComponent, MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// 按需注册：全量 echarts 约 1MB，这里只打包用到的折线 / 柱状与基础组件。
echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);

interface Props {
  option: echarts.EChartsCoreOption;
  height?: number;
}

// 轻量 ECharts 容器：初始化 / 跟随窗口 resize / 卸载时 dispose。
// 主题策略：不感知亮暗色，option 里统一用中性灰 + 主题橙（两种模式下都可读）。
export function EChart({ option, height = 300 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    inst.current = echarts.init(ref.current);
    // ResizeObserver 而非 window.resize：侧栏收起/展开改变容器宽度时
    // 并不触发窗口 resize 事件，只监听 window 会让图表保持旧尺寸被裁切
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      inst.current?.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    // notMerge=true：整份替换，避免旧 series 残留（刷新后型号增减时）
    inst.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} style={{ height }} />;
}
