import { createContext, useContext, type ReactNode } from "react";
import { useOmbro } from "../../hooks/useOmbro";
import { PACOTE, pacoteValido, type Pacote } from "./animacoes";

/**
 * O pacote de animações EM USO (o `applied` que o ombrod publica no providers.list), para todos os mascotes do app.
 * Quem decide quando ele muda é o ombrod (a troca segura, ombro/animacoes.py); cada mascote animado passa para ele no
 * próximo ponto seguro dele mesmo. Sem o ombrod, ou com um de antes desta opção: o padrão ("vivo").
 */
const PacoteContext = createContext<Pacote>(PACOTE);

export function PacoteDoOmbro({ children }: { children: ReactNode }) {
  const { snapshot } = useOmbro();
  const aplicado = pacoteValido(snapshot?.providers?.animation?.applied);
  return <PacoteContext.Provider value={aplicado}>{children}</PacoteContext.Provider>;
}

export function usePacoteAplicado(): Pacote {
  return useContext(PacoteContext);
}
