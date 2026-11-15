import { Component, type ReactNode } from "react";
import { Notice } from "./ui";

/** Um erro numa seção fica nela: a navegação e as outras seções seguem funcionando. */
export class PageBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };

  static getDerivedStateFromError(e: unknown) {
    return { error: e instanceof Error ? e.message : String(e) };
  }

  render() {
    if (this.state.error) {
      return (
        <Notice tone="critical" title="Esta seção encontrou um erro">
          {this.state.error}. As outras seções seguem funcionando; se repetir, copie o relatório em Diagnóstico e abra uma issue.
        </Notice>
      );
    }
    return this.props.children;
  }
}
