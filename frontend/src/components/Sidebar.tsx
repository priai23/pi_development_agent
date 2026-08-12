import Link from 'next/link';
import { Home, FolderGit2, Settings, MessageSquare, Database } from 'lucide-react';

export default function Sidebar() {
  return (
    <aside className="w-64 glass-panel flex flex-col h-screen border-r border-black/5 dark:border-white/10 z-10 sticky top-0">
      <div className="p-6">
        <h2 className="text-xl font-bold text-black dark:text-white tracking-tight">
          Primacy AI
        </h2>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 uppercase tracking-wider font-semibold">ERP Implementation</p>
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        <Link href="/" className="flex items-center space-x-3 px-3 py-2.5 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-black/5 dark:hover:bg-white/10 hover:text-black dark:hover:text-white transition-colors">
          <Home className="w-5 h-5" />
          <span>Dashboard</span>
        </Link>
        <Link href="/projects" className="flex items-center space-x-3 px-3 py-2.5 rounded-lg bg-blue-500 text-white shadow-sm border border-black/5 dark:border-white/10">
          <FolderGit2 className="w-5 h-5" />
          <span className="font-medium">Projects</span>
        </Link>
        <Link href="/instances" className="flex items-center space-x-3 px-3 py-2.5 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-black/5 dark:hover:bg-white/10 hover:text-black dark:hover:text-white transition-colors">
          <Database className="w-5 h-5" />
          <span>Instances</span>
        </Link>
        <Link href="/agent" className="flex items-center space-x-3 px-3 py-2.5 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-black/5 dark:hover:bg-white/10 hover:text-black dark:hover:text-white transition-colors">
          <MessageSquare className="w-5 h-5" />
          <span>Global Agent</span>
        </Link>
      </nav>

      <div className="p-4 border-t border-black/5 dark:border-white/10">
        <Link href="/settings" className="flex items-center space-x-3 px-3 py-2 rounded-lg text-gray-600 dark:text-gray-400 hover:text-black dark:hover:text-white transition-colors">
          <Settings className="w-5 h-5" />
          <span>Settings</span>
        </Link>
      </div>
    </aside>
  );
}
