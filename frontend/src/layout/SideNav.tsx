import { NavLink } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

interface NavItem {
  to: string;
  icon: string;
  label: string;
}

const navItems: NavItem[] = [
  { to: '/risk-feed', icon: 'dashboard', label: 'Risk Feed' },
  { to: '/search', icon: 'search', label: 'Search' },
  { to: '/coa', icon: 'account_tree', label: 'COA Workspace' },
  { to: '/monitoring', icon: 'monitoring', label: 'Monitoring' },
  { to: '/briefings', icon: 'present_to_all', label: 'Briefings' },
  { to: '/wargame', icon: 'public', label: 'Wargame' },
];

const adminNavItem: NavItem = { to: '/admin', icon: 'admin_panel_settings', label: 'Admin' };

const recentLinks = [
  { icon: 'history', label: 'Recent Queries', to: '/search' },
  { icon: 'security', label: 'Taiwan 2027', to: '/wargame' },
  { icon: 'directions_boat', label: 'Carrier Group 5', to: '/monitoring' },
];

export default function SideNav() {
  const { isAdmin } = useAuth();
  const items = isAdmin ? [...navItems, adminNavItem] : navItems;

  return (
    <aside className="fixed left-0 top-12 h-[calc(100vh-48px)] w-[240px] flex flex-col pt-4 bg-surface-dim border-r border-outline-variant z-40">
      <div className="px-6 mb-8">
        <h2 className="font-headline text-lg leading-tight text-on-surface">
          <span className="font-bold">Agile</span>{' '}
          <span className="font-normal">Defense</span>
        </h2>
        <p className="text-[10px] text-on-surface-variant uppercase tracking-widest">Tactical Node</p>
      </div>

      <nav className="flex-1 space-y-1">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-6 py-3 font-body text-xs font-medium uppercase tracking-tighter transition-all duration-150 ease-in-out ${
                isActive
                  ? 'text-accent-bright bg-surface-container border-l-2 border-accent'
                  : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
              }`
            }
          >
            <span className="material-symbols-outlined text-lg">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto border-t border-outline-variant py-4">
        <p className="px-6 mb-2 text-[10px] text-on-surface-variant uppercase tracking-widest font-bold">
          Recent Intelligence
        </p>
        {recentLinks.map((link) => (
          <NavLink
            key={link.label}
            to={link.to}
            className="flex items-center gap-3 px-6 py-2 text-on-surface-variant hover:text-on-surface text-[10px] uppercase font-medium"
          >
            <span className="material-symbols-outlined text-sm">{link.icon}</span>
            <span>{link.label}</span>
          </NavLink>
        ))}
      </div>
    </aside>
  );
}
