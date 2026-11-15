import { render } from "@testing-library/react";
import { vi } from "vitest";
import App from "../App";
import { OmbroProvider, type Page } from "../hooks/useOmbro";
import { setApiForTests, type OmbroApi } from "../services/api";
import { createMockApi } from "../services/mock";

/** A API de mentira com espiões em todos os métodos; `over` troca os que o teste precisa. */
export function fakeApi(over: Partial<OmbroApi> = {}): OmbroApi {
  const base = createMockApi();
  const api = { ...base, ...over } as OmbroApi;
  for (const k of Object.keys(api) as (keyof OmbroApi)[]) {
    const v = api[k];
    if (typeof v === "function") (api as unknown as Record<string, unknown>)[k] = vi.fn(v as (...a: unknown[]) => unknown);
  }
  return api;
}

export function renderApp(api: OmbroApi, page: Page = "dashboard") {
  setApiForTests(api);
  return render(
    <OmbroProvider initialPage={page}>
      <App />
    </OmbroProvider>,
  );
}
