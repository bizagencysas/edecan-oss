// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Screen Simulator                                               ║
// ║  Generates realistic HTML representations of app screens                   ║
// ║  based on extracted layout type, texts, colors and components              ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { MockupData, LayoutType } from './types';

export function buildScreenSimulation(mockup: MockupData): string {
  const { screen, darkMode } = mockup;
  const bg = darkMode ? '#000' : '#F8FAFC';
  const text = darkMode ? '#FAFAFA' : '#0F172A';
  const textSec = darkMode ? '#A1A1AA' : '#64748B';
  const textMut = darkMode ? '#71717A' : '#94A3B8';
  const card = darkMode ? '#141418' : '#FFFFFF';
  const border = darkMode ? '#27272A' : '#E2E8F0';
  const surface = darkMode ? '#18181C' : '#F1F5F9';
  const accent = mockup.gradientColors[0] || '#FFD639';
  const success = '#10B981';

  const layout = screen.estimatedLayout;
  const texts = screen.texts.length > 0 ? screen.texts : ['Welcome', 'Explore', 'Get Started'];

  const css = `width:100%;height:100%;background:${bg};padding:5%;font-family:'Inter',system-ui,sans-serif;overflow:hidden;display:flex;flex-direction:column;`;

  const statusBar = `<div style="display:flex;justify-content:space-between;align-items:center;padding:2% 0;margin-bottom:2%;">
    <span style="font-size:1.1em;font-weight:600;color:${text};">9:41</span>
    <div style="display:flex;gap:4px;align-items:center;">
      <div style="width:1.2em;height:0.7em;border:1.5px solid ${text};border-radius:2px;position:relative;"><div style="position:absolute;right:1px;top:1px;bottom:1px;left:30%;background:${success};border-radius:1px;"></div></div>
    </div>
  </div>`;

  const content = getLayoutContent(layout, texts, { bg, text, textSec, textMut, card, border, surface, accent, success, darkMode });

  return `<div style="${css}">${statusBar}${content}</div>`;
}

interface Colors {
  bg: string; text: string; textSec: string; textMut: string;
  card: string; border: string; surface: string; accent: string;
  success: string; darkMode: boolean;
}

function getLayoutContent(layout: LayoutType, texts: string[], c: Colors): string {
  switch (layout) {
    case 'dashboard': return dashboardScreen(texts, c);
    case 'wallet': return walletScreen(texts, c);
    case 'marketplace': return marketplaceScreen(texts, c);
    case 'profile': return profileScreen(texts, c);
    case 'settings': return settingsScreen(texts, c);
    case 'auth': return authScreen(texts, c);
    case 'chart': return chartScreen(texts, c);
    case 'detail': return detailScreen(texts, c);
    case 'list': return listScreen(texts, c);
    default: return genericScreen(texts, c);
  }
}

function dashboardScreen(t: string[], c: Colors): string {
  return `
    <div style="display:flex;align-items:center;gap:3%;margin-bottom:4%;">
      <div style="width:2.8em;height:2.8em;border-radius:50%;background:${c.accent};display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:1em;">JD</div>
      <div><div style="font-size:0.75em;color:${c.textMut};">Buenos días</div><div style="font-size:1.3em;font-weight:700;color:${c.text};">Carlos</div></div>
    </div>
    <div style="background:linear-gradient(135deg,#0F172A,#1E293B);border-radius:1.2em;padding:6%;margin-bottom:4%;">
      <div style="font-size:0.65em;color:#94A3B8;text-transform:uppercase;letter-spacing:0.1em;">Portafolio Total</div>
      <div style="font-size:2em;font-weight:800;color:#fff;margin:2% 0;">$12,450.00</div>
      <div style="font-size:0.85em;color:${c.success};font-weight:600;">↑ +8.5% ROI</div>
    </div>
    <div style="display:flex;gap:3%;margin-bottom:4%;">
      ${statCard('PENDIENTE', '$350.00', c)}
      ${statCard('RECIBIDO', '$1,200.00', c)}
    </div>
    <div style="background:${c.darkMode?'#0a2e1a':'#e6f7ed'};border:1.5px solid ${c.success}30;border-radius:0.8em;padding:4%;margin-bottom:4%;display:flex;align-items:center;gap:3%;">
      <div style="width:0.6em;height:0.6em;border-radius:50%;background:${c.success};"></div>
      <div style="flex:1;"><div style="font-size:0.6em;color:${c.success};font-weight:700;text-transform:uppercase;">Tu dinero está generando valor</div><div style="font-size:1.2em;color:${c.success};font-weight:800;margin-top:1%;">+$0.42 hoy</div></div>
    </div>
    <div style="font-size:0.85em;font-weight:600;color:${c.text};margin-bottom:2%;">Acciones Rápidas</div>
    <div style="display:flex;gap:2%;flex-wrap:wrap;">
      ${quickAction('Invertir', c.success, c)}${quickAction('Wallet', '#6366F1', c)}${quickAction('Ahorros', '#10B981', c)}${quickAction('Retos', '#8B5CF6', c)}
    </div>`;
}

function walletScreen(t: string[], c: Colors): string {
  return `
    <div style="font-size:1.3em;font-weight:700;color:${c.text};margin-bottom:4%;">Wallet</div>
    <div style="background:linear-gradient(135deg,#6366F1,#8B5CF6);border-radius:1.2em;padding:6%;margin-bottom:4%;">
      <div style="font-size:0.65em;color:rgba(255,255,255,0.7);">Balance Disponible</div>
      <div style="font-size:2.2em;font-weight:800;color:#fff;margin:2% 0;">$3,240.50</div>
      <div style="display:flex;gap:3%;margin-top:4%;">
        <div style="flex:1;background:rgba(255,255,255,0.2);border-radius:0.6em;padding:3%;text-align:center;color:#fff;font-weight:600;font-size:0.8em;">Depositar</div>
        <div style="flex:1;background:rgba(255,255,255,0.2);border-radius:0.6em;padding:3%;text-align:center;color:#fff;font-weight:600;font-size:0.8em;">Retirar</div>
      </div>
    </div>
    <div style="font-size:0.85em;font-weight:600;color:${c.text};margin-bottom:3%;">Transacciones</div>
    ${txRow('Inversión — Solar Grid', '-$500.00', '#EF4444', c)}
    ${txRow('Retorno — Coffee Co', '+$125.00', c.success, c)}
    ${txRow('Depósito', '+$1,000.00', c.success, c)}
    ${txRow('Inversión — Tech Fund', '-$250.00', '#EF4444', c)}`;
}

function marketplaceScreen(t: string[], c: Colors): string {
  return `
    <div style="font-size:1.3em;font-weight:700;color:${c.text};margin-bottom:3%;">Mercado</div>
    <div style="background:${c.surface};border-radius:0.6em;padding:3% 4%;margin-bottom:4%;display:flex;align-items:center;gap:3%;">
      <span style="color:${c.textMut};font-size:0.85em;">🔍</span>
      <span style="color:${c.textMut};font-size:0.85em;">Buscar oportunidades...</span>
    </div>
    ${projectCard('Solar Energy Grid', 'Energía Renovable', '$50,000', '72%', '#F97316', c)}
    ${projectCard('Urban Coffee Co', 'Alimentos', '$25,000', '45%', '#10B981', c)}
    ${projectCard('FinTech Bridge', 'Tecnología', '$100,000', '89%', '#6366F1', c)}`;
}

function profileScreen(t: string[], c: Colors): string {
  return `
    <div style="display:flex;flex-direction:column;align-items:center;margin-bottom:5%;">
      <div style="width:4em;height:4em;border-radius:50%;background:linear-gradient(135deg,${c.accent},#F97316);display:flex;align-items:center;justify-content:center;font-size:1.5em;font-weight:700;color:#fff;">JD</div>
      <div style="font-size:1.2em;font-weight:700;color:${c.text};margin-top:3%;">Juan Delgado</div>
      <div style="font-size:0.75em;color:${c.textSec};">Inversionista Verificado ✓</div>
    </div>
    <div style="display:flex;gap:3%;margin-bottom:5%;">
      ${miniStat('Inversiones', '8', c)}${miniStat('Retorno', '+12.5%', c)}${miniStat('Nivel', 'Pro', c)}
    </div>
    ${menuItem('Datos Personales', c)}${menuItem('Seguridad', c)}${menuItem('Métodos de Pago', c)}${menuItem('Documentos', c)}`;
}

function settingsScreen(t: string[], c: Colors): string {
  return `
    <div style="font-size:1.3em;font-weight:700;color:${c.text};margin-bottom:5%;">Ajustes</div>
    ${settingsItem('Modo Oscuro', true, c)}${settingsItem('Notificaciones', true, c)}${settingsItem('Biometría', false, c)}
    <div style="height:1px;background:${c.border};margin:3% 0;"></div>
    ${menuItem('Idioma', c)}${menuItem('Privacidad', c)}${menuItem('Soporte', c)}${menuItem('Términos y Condiciones', c)}
    <div style="height:1px;background:${c.border};margin:3% 0;"></div>
    <div style="padding:3% 0;color:#EF4444;font-size:0.85em;font-weight:600;">Cerrar Sesión</div>`;
}

function authScreen(t: string[], c: Colors): string {
  return `
    <div style="flex:1;display:flex;flex-direction:column;justify-content:center;align-items:center;">
      <div style="width:3.5em;height:3.5em;border-radius:1em;background:${c.accent};display:flex;align-items:center;justify-content:center;font-size:1.5em;font-weight:900;color:${c.darkMode?'#000':'#fff'};margin-bottom:5%;">F</div>
      <div style="font-size:1.5em;font-weight:800;color:${c.text};margin-bottom:2%;">Bienvenido</div>
      <div style="font-size:0.8em;color:${c.textSec};margin-bottom:6%;">Inicia sesión para continuar</div>
      <div style="width:85%;background:${c.surface};border:1.5px solid ${c.border};border-radius:0.6em;padding:3.5%;margin-bottom:3%;font-size:0.8em;color:${c.textMut};">correo@ejemplo.com</div>
      <div style="width:85%;background:${c.surface};border:1.5px solid ${c.border};border-radius:0.6em;padding:3.5%;margin-bottom:5%;font-size:0.8em;color:${c.textMut};">••••••••</div>
      <div style="width:85%;background:${c.accent};border-radius:0.6em;padding:3.5%;text-align:center;font-weight:700;color:${c.darkMode?'#000':'#fff'};font-size:0.9em;">Iniciar Sesión</div>
      <div style="font-size:0.75em;color:${c.textSec};margin-top:4%;">¿No tienes cuenta? <span style="color:${c.accent};font-weight:600;">Regístrate</span></div>
    </div>`;
}

function chartScreen(t: string[], c: Colors): string {
  return `
    <div style="font-size:1.3em;font-weight:700;color:${c.text};margin-bottom:4%;">Predicciones</div>
    <div style="background:${c.card};border:1px solid ${c.border};border-radius:1em;padding:5%;margin-bottom:4%;">
      <div style="font-size:0.7em;color:${c.textMut};text-transform:uppercase;">Retorno Proyectado</div>
      <div style="font-size:1.8em;font-weight:800;color:${c.success};margin:2% 0;">+18.4%</div>
      <svg viewBox="0 0 200 60" style="width:100%;margin-top:3%;">
        <polyline points="0,50 20,45 40,48 60,35 80,38 100,25 120,20 140,28 160,15 180,10 200,5" fill="none" stroke="${c.success}" stroke-width="2.5" stroke-linecap="round"/>
        <polyline points="0,50 20,45 40,48 60,35 80,38 100,25 120,20 140,28 160,15 180,10 200,5" fill="url(#grad)" stroke="none"/>
        <defs><linearGradient id="grad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${c.success}" stop-opacity="0.3"/><stop offset="100%" stop-color="${c.success}" stop-opacity="0"/></linearGradient></defs>
      </svg>
    </div>
    ${miniStat('6 meses', '+12%', c)}`;
}

function detailScreen(t: string[], c: Colors): string {
  return `
    <div style="font-size:1.3em;font-weight:700;color:${c.text};margin-bottom:2%;">${t[0] || 'Proyecto Solar'}</div>
    <div style="font-size:0.75em;color:${c.textSec};margin-bottom:4%;">Energía Renovable · 12 meses</div>
    <div style="background:${c.card};border:1px solid ${c.border};border-radius:1em;padding:5%;margin-bottom:3%;">
      <div style="display:flex;justify-content:space-between;margin-bottom:3%;">
        <div><div style="font-size:0.6em;color:${c.textMut};">META</div><div style="font-size:1.1em;font-weight:700;color:${c.text};">$50,000</div></div>
        <div style="text-align:right;"><div style="font-size:0.6em;color:${c.textMut};">RETORNO</div><div style="font-size:1.1em;font-weight:700;color:${c.success};">18% anual</div></div>
      </div>
      <div style="height:6px;background:${c.surface};border-radius:3px;overflow:hidden;"><div style="width:72%;height:100%;background:#F97316;border-radius:3px;"></div></div>
      <div style="font-size:0.7em;color:${c.textSec};margin-top:2%;">72% financiado</div>
    </div>
    <div style="background:${c.accent};border-radius:0.8em;padding:4%;text-align:center;font-weight:700;color:${c.darkMode?'#000':'#fff'};font-size:0.95em;">Invertir Ahora</div>`;
}

function listScreen(t: string[], c: Colors): string { return genericScreen(t, c); }

function genericScreen(t: string[], c: Colors): string {
  const title = t[0] || 'Pantalla';
  return `
    <div style="font-size:1.3em;font-weight:700;color:${c.text};margin-bottom:4%;">${title}</div>
    <div style="background:${c.card};border:1px solid ${c.border};border-radius:1em;padding:5%;margin-bottom:3%;">
      <div style="font-size:0.85em;color:${c.text};font-weight:600;margin-bottom:2%;">${t[1] || 'Información'}</div>
      <div style="font-size:0.75em;color:${c.textSec};line-height:1.5;">${t[2] || 'Contenido de la aplicación con datos en tiempo real.'}</div>
    </div>
    <div style="background:${c.card};border:1px solid ${c.border};border-radius:1em;padding:5%;">
      <div style="font-size:0.85em;color:${c.text};font-weight:600;">${t[3] || 'Detalles'}</div>
    </div>`;
}

// ── UI Helper Components ──

function statCard(label: string, value: string, c: Colors): string {
  return `<div style="flex:1;background:${c.card};border:1px solid ${c.border};border-radius:0.8em;padding:4%;">
    <div style="font-size:0.55em;color:${c.textMut};text-transform:uppercase;letter-spacing:0.08em;">${label}</div>
    <div style="font-size:1.1em;font-weight:700;color:${c.text};margin-top:2%;">${value}</div>
  </div>`;
}

function quickAction(label: string, color: string, c: Colors): string {
  return `<div style="width:22%;background:${c.card};border:1px solid ${c.border};border-radius:0.7em;padding:3%;text-align:center;">
    <div style="width:2em;height:2em;border-radius:50%;background:${color}18;margin:0 auto 5%;display:flex;align-items:center;justify-content:center;">
      <div style="width:0.7em;height:0.7em;background:${color};border-radius:2px;"></div>
    </div>
    <div style="font-size:0.55em;font-weight:600;color:${c.text};">${label}</div>
  </div>`;
}

function txRow(label: string, amount: string, color: string, c: Colors): string {
  return `<div style="display:flex;justify-content:space-between;align-items:center;padding:3% 0;border-bottom:1px solid ${c.border};">
    <span style="font-size:0.8em;color:${c.text};">${label}</span>
    <span style="font-size:0.8em;font-weight:700;color:${color};">${amount}</span>
  </div>`;
}

function projectCard(name: string, cat: string, target: string, pct: string, color: string, c: Colors): string {
  return `<div style="background:${c.card};border:1px solid ${c.border};border-radius:1em;padding:4%;margin-bottom:3%;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2%;">
      <div><div style="font-size:0.9em;font-weight:700;color:${c.text};">${name}</div><div style="font-size:0.65em;color:${c.textSec};">${cat}</div></div>
      <div style="background:${color}1A;padding:1.5% 3%;border-radius:2em;font-size:0.65em;font-weight:700;color:${color};">${pct}</div>
    </div>
    <div style="height:5px;background:${c.surface};border-radius:3px;overflow:hidden;"><div style="width:${pct};height:100%;background:${color};border-radius:3px;"></div></div>
    <div style="font-size:0.65em;color:${c.textSec};margin-top:2%;">${target} meta</div>
  </div>`;
}

function miniStat(label: string, value: string, c: Colors): string {
  return `<div style="flex:1;text-align:center;background:${c.card};border:1px solid ${c.border};border-radius:0.7em;padding:3%;">
    <div style="font-size:1em;font-weight:800;color:${c.text};">${value}</div>
    <div style="font-size:0.6em;color:${c.textMut};margin-top:1%;">${label}</div>
  </div>`;
}

function menuItem(label: string, c: Colors): string {
  return `<div style="display:flex;justify-content:space-between;align-items:center;padding:3.5% 0;border-bottom:1px solid ${c.border};">
    <span style="font-size:0.85em;color:${c.text};">${label}</span>
    <span style="font-size:0.85em;color:${c.textMut};">›</span>
  </div>`;
}

function settingsItem(label: string, on: boolean, c: Colors): string {
  return `<div style="display:flex;justify-content:space-between;align-items:center;padding:3.5% 0;border-bottom:1px solid ${c.border};">
    <span style="font-size:0.85em;color:${c.text};">${label}</span>
    <div style="width:2.5em;height:1.4em;border-radius:0.7em;background:${on?c.success:c.surface};position:relative;">
      <div style="position:absolute;top:2px;${on?'right:2px':'left:2px'};width:1em;height:1em;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.2);"></div>
    </div>
  </div>`;
}
