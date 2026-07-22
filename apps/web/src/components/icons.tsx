/**
 * Set mínimo de iconos de línea (24x24, `currentColor`) para no depender de
 * ninguna librería de iconos — el proyecto no trae una (ver `package.json`).
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function base(children: React.ReactNode, props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export const ChatIcon = (p: IconProps) => base(<path d="M4 4h16v11H8l-4 4V4Z" />, p);

export const SparklesIcon = (p: IconProps) =>
  base(
    <>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
    </>,
    p,
  );

export const BrainIcon = (p: IconProps) =>
  base(
    <>
      <path d="M9 4a3 3 0 0 0-3 3v.3A3 3 0 0 0 4.5 10 3 3 0 0 0 6 15.5V16a3 3 0 0 0 3 3" />
      <path d="M15 4a3 3 0 0 1 3 3v.3A3 3 0 0 1 19.5 10 3 3 0 0 1 18 15.5V16a3 3 0 0 1-3 3" />
      <path d="M9 4v15M15 4v15" />
    </>,
    p,
  );

export const PlugIcon = (p: IconProps) =>
  base(
    <>
      <path d="M9 2v5M15 2v5M7 7h10v3a5 5 0 0 1-10 0V7Z" />
      <path d="M12 15v3M9 21h6" />
    </>,
    p,
  );

export const FileIcon = (p: IconProps) =>
  base(
    <>
      <path d="M7 3h7l5 5v13H7V3Z" />
      <path d="M14 3v5h5" />
    </>,
    p,
  );

export const BellIcon = (p: IconProps) =>
  base(
    <>
      <path d="M6 10a6 6 0 1 1 12 0c0 4 1.5 5.5 1.5 5.5H4.5S6 14 6 10Z" />
      <path d="M10 19a2 2 0 0 0 4 0" />
    </>,
    p,
  );

export const UsersIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20c0-3 2.7-5 6-5s6 2 6 5" />
      <path d="M16 5.2A3 3 0 1 1 17 11" />
      <path d="M15 15c2.8.3 4.9 2.1 5 5" />
    </>,
    p,
  );

export const WalletIcon = (p: IconProps) =>
  base(
    <>
      <path d="M3 7a2 2 0 0 1 2-2h13a1 1 0 0 1 1 1v3" />
      <path d="M3 7v10a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1h-4a2.5 2.5 0 0 0 0 5h5" />
    </>,
    p,
  );

export const CreditCardIcon = (p: IconProps) =>
  base(
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 10h18M7 15h4" />
    </>,
    p,
  );

export const SettingsIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 13a7.97 7.97 0 0 0 0-2l2-1.5-2-3.4-2.4.7a8 8 0 0 0-1.7-1L14.8 3h-4l-.5 2.8a8 8 0 0 0-1.7 1l-2.4-.7-2 3.4L6.2 11a7.97 7.97 0 0 0 0 2l-2 1.5 2 3.4 2.4-.7a8 8 0 0 0 1.7 1l.5 2.8h4l.5-2.8a8 8 0 0 0 1.7-1l2.4.7 2-3.4-2-1.5Z" />
    </>,
    p,
  );

export const LogOutIcon = (p: IconProps) =>
  base(
    <>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5M21 12H9" />
    </>,
    p,
  );

export const SendIcon = (p: IconProps) =>
  base(<path d="M22 2 11 13M22 2 15 22l-4-9-9-4 20-7Z" />, p);

export const MicIcon = (p: IconProps) =>
  base(
    <>
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v4M9 22h6" />
    </>,
    p,
  );

export const PlayIcon = (p: IconProps) => base(<path d="M6 4l14 8-14 8V4Z" />, p);

export const SquareIcon = (p: IconProps) => base(<rect x="5" y="5" width="14" height="14" rx="2" />, p);

export const TrashIcon = (p: IconProps) =>
  base(
    <>
      <path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M7 7l1 13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-13" />
    </>,
    p,
  );

export const PlusIcon = (p: IconProps) => base(<path d="M12 5v14M5 12h14" />, p);

export const XIcon = (p: IconProps) => base(<path d="M18 6 6 18M6 6l12 12" />, p);

export const CheckIcon = (p: IconProps) => base(<path d="M20 6 9 17l-5-5" />, p);

export const RetryIcon = (p: IconProps) =>
  base(
    <>
      <path d="M20 7v5h-5" />
      <path d="M19 12a7 7 0 1 0-2 5" />
    </>,
    p,
  );

export const PhoneIcon = (p: IconProps) =>
  base(
    <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c1 .3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z" />,
    p,
  );

export const ChevronDownIcon = (p: IconProps) => base(<path d="m6 9 6 6 6-6" />, p);

export const MenuIcon = (p: IconProps) => base(<path d="M4 6h16M4 12h16M4 18h16" />, p);

export const UploadIcon = (p: IconProps) =>
  base(
    <>
      <path d="M12 16V4M7 9l5-5 5 5" />
      <path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </>,
    p,
  );

export const SearchIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </>,
    p,
  );

export const GridIcon = (p: IconProps) =>
  base(
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>,
    p,
  );

export const SunIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>,
    p,
  );

export const MoonIcon = (p: IconProps) =>
  base(<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />, p);

// --- v2 (ROADMAP_V2.md §7.10, dueño WP-V2-01) -------------------------------
// Íconos nuevos para `nav-items.ts`: Misiones, Automatizaciones, IDE, Remoto,
// Órdenes, Negocios, Perfil vivo.

export const RocketIcon = (p: IconProps) =>
  base(
    <>
      <path d="M12 2c2.5 2 4 5.5 4 9 0 2-1 4-1 4H9s-1-2-1-4c0-3.5 1.5-7 4-9Z" />
      <circle cx="12" cy="9" r="1.5" />
      <path d="M9 15l-2 5 3-2M15 15l2 5-3-2" />
    </>,
    p,
  );

export const ZapIcon = (p: IconProps) => base(<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" />, p);

export const CodeIcon = (p: IconProps) => base(<path d="M9 8 4 12l5 4M15 8l5 4-5 4" />, p);

export const MonitorIcon = (p: IconProps) =>
  base(
    <>
      <rect x="3" y="4" width="18" height="12" rx="1.5" />
      <path d="M8 20h8M12 16v4" />
    </>,
    p,
  );

export const CartIcon = (p: IconProps) =>
  base(
    <>
      <path d="M3 4h2l2.4 12.2a2 2 0 0 0 2 1.8h7.2a2 2 0 0 0 2-1.6L20 9H6" />
      <circle cx="9" cy="20" r="1.3" />
      <circle cx="17" cy="20" r="1.3" />
    </>,
    p,
  );

export const BriefcaseIcon = (p: IconProps) =>
  base(
    <>
      <rect x="3" y="7" width="18" height="12" rx="1.5" />
      <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18" />
    </>,
    p,
  );

export const IdCardIcon = (p: IconProps) =>
  base(
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="8.5" cy="11" r="2" />
      <path d="M6 16c.5-1.5 1.8-2.3 2.5-2.3S11 14.5 11 16M14 9.5h5M14 13h5M14 16h3" />
    </>,
    p,
  );

// --- v3 (ARCHITECTURE.md §12, dueño WP-V3-01) -------------------------------
// Íconos nuevos para `nav-items.ts`: Configuración (credenciales) y Skills.

export const KeyIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="8" cy="15" r="4" />
      <path d="M11 12 20 3M17 6l2.5 2.5M14 9l2 2" />
    </>,
    p,
  );

export const PuzzleIcon = (p: IconProps) =>
  base(<path d="M6 6h4v-2h2v2h4v4h-2v2h2v4h-10z" />, p);

// --- v4 (ARCHITECTURE.md §13, dueño WP-V4-01) -------------------------------
// Íconos nuevos para `nav-items.ts`: Inventario y Mensajes.

export const BoxIcon = (p: IconProps) =>
  base(
    <>
      <path d="M3 8 12 3l9 5-9 5-9-5Z" />
      <path d="M3 8v9l9 5 9-5V8M12 13v9" />
    </>,
    p,
  );

export const InboxIcon = (p: IconProps) =>
  base(
    <>
      <path d="M3 12h5l1.5 3h5L16 12h5" />
      <path d="M5.5 6h13L21 12v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6L5.5 6Z" />
    </>,
    p,
  );

// --- v5 (ARCHITECTURE.md §14, dueño WP-V5-01) -------------------------------
// Íconos nuevos para `nav-items.ts`: RRHH y Viajes. "Voz" reutiliza el
// `MicIcon` ya existente (arriba, usado por el composer de chat y por la
// tarjeta de STT en Configuración) en vez de declarar uno nuevo con el mismo
// nombre — este archivo ya exporta `MicIcon`, así que redeclararlo rompería
// la compilación (identificador duplicado).

export const TeamIcon = (p: IconProps) =>
  base(
    <>
      <circle cx="12" cy="5" r="2.3" />
      <circle cx="5.5" cy="18" r="2.3" />
      <circle cx="18.5" cy="18" r="2.3" />
      <path d="M12 7.3V11M12 11 6.8 15.8M12 11l5.2 4.8" />
    </>,
    p,
  );

export const PlaneIcon = (p: IconProps) =>
  base(
    <>
      <path d="M12 2v18" />
      <path d="M4 13l8-2.5 8 2.5" />
      <path d="M9 18l3-1 3 1" />
    </>,
    p,
  );

// --- v6 (ARCHITECTURE.md §15, dueño WP-V6-01) -------------------------------
// Íconos nuevos para `nav-items.ts`: Reuniones y Analista.

export const VideoIcon = (p: IconProps) =>
  base(
    <>
      <rect x="3" y="6" width="13" height="12" rx="2" />
      <path d="M16 10.5 21 7v10l-5-3.5" />
    </>,
    p,
  );

export const ChartBarIcon = (p: IconProps) =>
  base(
    <>
      <path d="M4 20V10M10 20V4M16 20v-7M4 20h16" />
    </>,
    p,
  );
