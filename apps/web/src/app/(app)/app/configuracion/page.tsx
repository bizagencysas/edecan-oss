import { redirect } from "next/navigation";

/** Alias histórico: la experiencia única de conexiones vive en Ajustes. */
export default function ConfiguracionAliasPage() {
  redirect("/app/ajustes#conexiones");
}
