import {
  BellIcon,
  BoxIcon,
  BrainIcon,
  BriefcaseIcon,
  CartIcon,
  ChartBarIcon,
  ChatIcon,
  CodeIcon,
  CreditCardIcon,
  FileIcon,
  GridIcon,
  IdCardIcon,
  InboxIcon,
  KeyIcon,
  MicIcon,
  MonitorIcon,
  PlaneIcon,
  PlugIcon,
  PuzzleIcon,
  RocketIcon,
  SendIcon,
  SettingsIcon,
  SparklesIcon,
  TeamIcon,
  UsersIcon,
  VideoIcon,
  WalletIcon,
  ZapIcon,
} from "@/components/icons";

export interface NavItem {
  href: string;
  label: string;
  icon: (props: { className?: string }) => React.ReactElement;
}

/**
 * Rutas reales bajo `src/app/(app)/app/*` (el route group `(app)` no aporta
 * segmento de URL). Las notas `v2`/`v3`/`v4`/`v5`/`v6`/`v7` de abajo son
 * historial de qué paquete de trabajo sumó cada entrada — todas las páginas
 * listadas aquí YA ATERRIZARON (verificado por WP-V7-09,
 * `docs/cumplimiento/barrido-v7-ux.md`: cada `href` tiene su carpeta real
 * bajo `app/(app)/app/`); las notas viejas de "enlace puede dar 404 hasta
 * entonces" que documentaban aterrizajes parciales en paralelo ya no
 * aplican y se quitaron de aquí.
 */
export const NAV_ITEMS: NavItem[] = [
  { href: "/app", label: "Chat", icon: ChatIcon },
  { href: "/app/persona", label: "Persona", icon: SparklesIcon },
  { href: "/app/memoria", label: "Memoria", icon: BrainIcon },
  { href: "/app/conectores", label: "Conectores", icon: PlugIcon },
  // v4 (ARCHITECTURE.md §13, dueño WP-V4-01): junto a Conectores.
  { href: "/app/mensajes", label: "Mensajes", icon: InboxIcon },
  // v6 (ARCHITECTURE.md §15, dueño WP-V6-01): junto a Mensajes.
  { href: "/app/reuniones", label: "Reuniones", icon: VideoIcon },
  { href: "/app/archivos", label: "Archivos", icon: FileIcon },
  { href: "/app/recordatorios", label: "Recordatorios", icon: BellIcon },
  { href: "/app/contactos", label: "Contactos", icon: UsersIcon },
  { href: "/app/finanzas", label: "Finanzas", icon: WalletIcon },
  { href: "/app/panel", label: "Panel", icon: GridIcon },
  // v6 (ARCHITECTURE.md §15, dueño WP-V6-01): junto a Panel.
  { href: "/app/analista", label: "Analista", icon: ChartBarIcon },
  // --- v2 (ROADMAP_V2.md §7.10, dueño WP-V2-01) -----------------------------
  { href: "/app/misiones", label: "Misiones", icon: RocketIcon },
  { href: "/app/automatizaciones", label: "Automatizaciones", icon: ZapIcon },
  { href: "/app/ide", label: "IDE", icon: CodeIcon },
  { href: "/app/remoto", label: "Remoto", icon: MonitorIcon },
  { href: "/app/ordenes", label: "Órdenes", icon: CartIcon },
  // v7 (docs/cumplimiento/barrido-v7-ux.md, WP-V7-09): junto a Órdenes — el
  // router `/v1/ads` (v4, WP-V4-07, `ARCHITECTURE.md` §13) estaba montado
  // sin ninguna página que lo consumiera; esta auditoría construyó
  // `/app/ads` siguiendo el mismo patrón borrador→confirmación con
  // guardrail de dinero que Órdenes.
  { href: "/app/ads", label: "Ads", icon: SendIcon },
  // v5 (ARCHITECTURE.md §14, dueño WP-V5-01): junto a Órdenes/Ads.
  { href: "/app/viajes", label: "Viajes", icon: PlaneIcon },
  { href: "/app/negocios", label: "Negocios", icon: BriefcaseIcon },
  // v4 (ARCHITECTURE.md §13, dueño WP-V4-01): junto a Negocios.
  { href: "/app/inventario", label: "Inventario", icon: BoxIcon },
  // v5 (ARCHITECTURE.md §14, dueño WP-V5-01): junto a Inventario.
  { href: "/app/rrhh", label: "RRHH", icon: TeamIcon },
  { href: "/app/perfil-vivo", label: "Perfil vivo", icon: IdCardIcon },
  // --- v3 (ARCHITECTURE.md §12, dueño WP-V3-01) -----------------------------
  { href: "/app/configuracion", label: "Configuración", icon: KeyIcon },
  // v5 (ARCHITECTURE.md §14, dueño WP-V5-01): junto a Configuración —
  // página de voz avanzada (clonación, podcasts, efectos de sonido).
  { href: "/app/voz", label: "Voz", icon: MicIcon },
  { href: "/app/skills", label: "Skills", icon: PuzzleIcon },
  { href: "/app/ajustes", label: "Ajustes", icon: SettingsIcon },
  { href: "/app/facturacion", label: "Facturación", icon: CreditCardIcon },
];
