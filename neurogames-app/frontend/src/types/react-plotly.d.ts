/* eslint-disable @typescript-eslint/no-explicit-any */
declare module "react-plotly.js" {
  import { Component } from "react";

  interface PlotParams {
    data: any[];
    layout?: any;
    config?: any;
    style?: React.CSSProperties;
    className?: string;
    useResizeHandler?: boolean;
    onInitialized?: (figure: any, graphDiv: any) => void;
    onUpdate?: (figure: any, graphDiv: any) => void;
    onPurge?: (figure: any, graphDiv: any) => void;
    onError?: (err: any) => void;
  }

  export default class Plot extends Component<PlotParams> {}
}
