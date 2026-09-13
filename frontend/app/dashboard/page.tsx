import StatPanel from "@/components/dashboard/StatPanel";
import MapArea from "@/components/dashboard/MapArea";
import RightPanel from "@/components/dashboard/RightPanel";

export default function DashboardPage() {
  return (
    <main className="flex-1 flex w-full relative">
      <StatPanel />
      <MapArea />
      <RightPanel />
    </main>
  );
}
