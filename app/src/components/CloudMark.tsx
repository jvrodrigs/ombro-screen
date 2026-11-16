import cloudSvg from "../assets/cloud.svg?raw";

/** O Ombro Cloud (gerado de avatars/defs/ombro-cloud.json por app/scripts/gen_tray_icon.py): a forma do mascote. */
export function CloudMark({ size = 22 }: { size?: number }) {
  return <span className="cloud-mark" style={{ width: size, height: size * 0.8 }} dangerouslySetInnerHTML={{ __html: cloudSvg }} />;
}
