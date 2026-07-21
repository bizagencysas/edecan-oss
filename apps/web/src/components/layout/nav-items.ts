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

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * La experiencia normal de Edecan tiene una sola puerta de entrada y dos
 * lugares de apoyo. Las capacidades concretas siguen existiendo, pero no
 * obligan a la persona a aprender la arquitectura del producto.
 */
export const PRIMARY_NAV_ITEMS: NavItem[] = [
  { href: "/app", label: "Edecan", icon: ChatIcon },
  { href: "/app/actividad", label: "Actividad", icon: BellIcon },
  { href: "/app/ajustes", label: "Ajustes", icon: SettingsIcon },
];

/**
 * Rutas especializadas preservadas para quien las necesita. Se muestran
 * únicamente después de activar "Modo avanzado" y también se enlazan desde
 * Actividad/Ajustes. Ocultarlas no cambia permisos, URLs ni APIs.
 */
export const ADVANCED_NAV_GROUPS: NavGroup[] = [
  {
    label: "Tu asistente",
    items: [
      { href: "/app/persona", label: "Personalidad", icon: SparklesIcon },
      { href: "/app/memoria", label: "Memoria", icon: BrainIcon },
      { href: "/app/voz", label: "Voz", icon: MicIcon },
      { href: "/app/perfil-vivo", label: "Perfil vivo", icon: IdCardIcon },
    ],
  },
  {
    label: "Trabajo",
    items: [
      { href: "/app/misiones", label: "Misiones", icon: RocketIcon },
      { href: "/app/automatizaciones", label: "Automatizaciones", icon: ZapIcon },
      { href: "/app/recordatorios", label: "Recordatorios", icon: BellIcon },
      { href: "/app/mensajes", label: "Mensajes", icon: InboxIcon },
      { href: "/app/reuniones", label: "Reuniones", icon: VideoIcon },
      { href: "/app/archivos", label: "Archivos", icon: FileIcon },
      { href: "/app/contactos", label: "Contactos", icon: UsersIcon },
    ],
  },
  {
    label: "Capacidades",
    items: [
      { href: "/app/conectores", label: "Conectores", icon: PlugIcon },
      { href: "/app/finanzas", label: "Finanzas", icon: WalletIcon },
      { href: "/app/panel", label: "Panel", icon: GridIcon },
      { href: "/app/analista", label: "Analista", icon: ChartBarIcon },
      { href: "/app/ordenes", label: "Órdenes", icon: CartIcon },
      { href: "/app/ads", label: "Ads", icon: SendIcon },
      { href: "/app/viajes", label: "Viajes", icon: PlaneIcon },
      { href: "/app/negocios", label: "Negocios", icon: BriefcaseIcon },
      { href: "/app/inventario", label: "Inventario", icon: BoxIcon },
      { href: "/app/rrhh", label: "RRHH", icon: TeamIcon },
    ],
  },
  {
    label: "Herramientas técnicas",
    items: [
      { href: "/app/configuracion", label: "Proveedores de IA", icon: KeyIcon },
      { href: "/app/skills", label: "Skills", icon: PuzzleIcon },
      { href: "/app/ide", label: "IDE", icon: CodeIcon },
      { href: "/app/remoto", label: "Control remoto", icon: MonitorIcon },
      { href: "/app/facturacion", label: "Facturación", icon: CreditCardIcon },
    ],
  },
];

/** Alias compatible para cualquier consumidor histórico de la navegación. */
export const NAV_ITEMS = PRIMARY_NAV_ITEMS;

export const ADVANCED_NAV_ITEMS = ADVANCED_NAV_GROUPS.flatMap((group) => group.items);

export function isNavItemActive(pathname: string | null, href: string): boolean {
  if (href === "/app") return pathname === "/app";
  return pathname === href || Boolean(pathname?.startsWith(`${href}/`));
}
