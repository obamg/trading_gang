import { Outlet } from "react-router-dom";
import { TopBar } from "./TopBar";
import { Sidebar } from "./Sidebar";

export function AppLayout() {
  return (
    <div className="flex h-full flex-col bg-canvas">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
