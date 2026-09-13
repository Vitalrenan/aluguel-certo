import React from 'react';
import Link from 'next/link';
import { LayoutGrid, SlidersHorizontal, Globe, CreditCard, Settings, Bell, CircleDot } from 'lucide-react';

export default function Sidebar() {
  return (
    <aside className="w-20 h-full bg-surface flex flex-col items-center py-8 shadow-card z-20 border-r border-black/5 relative">
      {/* Logo */}
      <Link href="/dashboard" className="mb-12">
        <div className="w-10 h-10 rounded-full bg-gradient-to-tr from-brand to-brand flex items-center justify-center shadow-card">
           <CircleDot className="text-white w-6 h-6" />
        </div>
      </Link>

      {/* Main Nav */}
      <nav className="flex flex-col gap-8 flex-1 w-full items-center">
        <NavItem icon={<LayoutGrid />} active />
        <NavItem icon={<SlidersHorizontal />} />
        <NavItem icon={<Globe />} />
        <NavItem icon={<CreditCard />} />
        <NavItem icon={<Settings />} />
      </nav>

      {/* Bottom Nav */}
      <div className="flex flex-col gap-8 w-full items-center">
        <button className="w-10 h-10 rounded-full overflow-hidden border-2 border-white shadow-card">
          {/* Avatar Placeholder */}
          <img src="https://i.pravatar.cc/100?img=11" alt="User Avatar" className="w-full h-full object-cover" />
        </button>
        <button className="text-ink-faint hover:text-brand transition-colors relative">
          <Bell className="w-6 h-6" />
          <span className="absolute top-0 right-0 w-2 h-2 bg-res-alerta rounded-full border border-white"></span>
        </button>
      </div>
    </aside>
  );
}

function NavItem({ icon, active = false }: { icon: React.ReactElement<{ className?: string }>; active?: boolean }) {
  return (
    <Link href="#" className={`p-3 rounded-[var(--radius-control)] transition-all duration-200 ${active ? 'bg-brand/10 text-brand' : 'text-ink-faint hover:bg-surface-mute hover:text-ink-soft'}`}>
      {React.cloneElement(icon, { className: "w-6 h-6" })}
    </Link>
  );
}
