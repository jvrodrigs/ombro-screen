import cloudSvg from "../assets/cloud.svg?raw";

/** O cloud do Ombro (gerado de dados_cloud.py por scripts/gen_tray_icon.py): a mesma forma do mascote da tela. */
export function CloudMark({ size = 22 }: { size?: number }) {
  return <span className="cloud-mark" style={{ width: size, height: size * 0.8 }} dangerouslySetInnerHTML={{ __html: cloudSvg }} />;
}
