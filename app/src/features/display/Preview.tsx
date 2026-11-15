import { useEffect, useRef, useState } from "react";
import { MonitorOff } from "lucide-react";
import { Segmented } from "../../components/ui";
import { useInterval } from "../../hooks/useAsync";
import { useOmbro } from "../../hooks/useOmbro";
import { ago } from "../../lib/format";
import type { Preview as PreviewData } from "../../types/ombro";

type Zoom = "fit" | "1" | "2";

/** O último quadro que o ombrod mandou para a tela (o mesmo renderer da USB): nada é redesenhado aqui. */
export function Preview({ intervalMs = 1000, zoomable = true }: { intervalMs?: number; zoomable?: boolean }) {
  const { api, snapshot } = useOmbro();
  const [data, setData] = useState<PreviewData | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [zoom, setZoom] = useState<Zoom>("fit");
  const conectado = snapshot?.daemon?.state === "connected";

  const buscar = () => {
    if (!conectado) return;
    api
      .displayPreview()
      .then((p) => {
        setData(p);
        setErro(null);
      })
      .catch(() => setErro("A prévia não respondeu."));
  };
  useInterval(buscar, intervalMs, conectado);
  const buscarRef = useRef(buscar);
  buscarRef.current = buscar;
  useEffect(() => {
    buscarRef.current(); // a primeira logo ao abrir (o intervalo só dispara depois de intervalMs)
  }, [conectado]);

  const w = data?.available ? data.width : 480;
  const h = data?.available ? data.height : 320;
  const largura = zoom === "fit" ? undefined : w * Number(zoom);

  return (
    <figure className="preview">
      <div className={`preview-frame zoom-${zoom}`} style={{ aspectRatio: `${w} / ${h}`, width: largura, maxWidth: zoom === "fit" ? 2 * w : "none" }}>
        {data?.available ? (
          <img
            src={`data:image/png;base64,${data.data_base64}`}
            width={w}
            height={h}
            alt="O que a telinha está mostrando agora"
            className={zoom === "2" ? "pixelated" : ""}
          />
        ) : (
          <div className="preview-empty">
            <MonitorOff size={22} aria-hidden />
            <span>{!conectado ? "Sem o serviço, não há prévia" : erro ?? "Esperando o primeiro quadro"}</span>
          </div>
        )}
      </div>
      <figcaption className="spread">
        <span className="muted small">
          {data?.available ? `${w}×${h} · quadro ${ago(data.captured_at)}` : `${w}×${h}`}
        </span>
        {zoomable && (
          <Segmented<Zoom>
            label="Tamanho da prévia"
            value={zoom}
            onChange={setZoom}
            options={[
              { value: "fit", label: "Ajustar" },
              { value: "1", label: "1×", title: "Tamanho real da tela (480×320)" },
              { value: "2", label: "2×" },
            ]}
          />
        )}
      </figcaption>
    </figure>
  );
}
