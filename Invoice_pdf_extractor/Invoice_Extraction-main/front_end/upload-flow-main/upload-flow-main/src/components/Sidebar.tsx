import { ChevronLeft } from "lucide-react";

export interface HistoryItem {
    id: string;
    date: string;
    filename: string;
    status: "success" | "processing" | "error";
}

interface SidebarProps {
    history: HistoryItem[];
    isOpen: boolean;
    onToggle: () => void;
}

export const Sidebar = ({ history, isOpen, onToggle }: SidebarProps) => {
    return (
        <div
            className={`bg-secondary h-screen text-sidebar-text flex flex-col border-r border-border/10 transition-all duration-300 relative overflow-hidden ${isOpen ? "w-72 opacity-100" : "w-0 opacity-0"
                }`}
        >
            <div className="p-6 h-full flex flex-col min-w-[288px]">
                <div className="flex items-center justify-between mb-8">
                    <h2 className="text-xl font-bold flex items-center gap-2 text-white">
                        Batch History
                    </h2>
                    <button
                        onClick={onToggle}
                        className="p-1 hover:bg-white/10 rounded-md transition-colors text-white/60 hover:text-white"
                    >
                        <ChevronLeft size={20} />
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto space-y-3 pr-2 scrollbar-thin scrollbar-thumb-white/10 hover:scrollbar-thumb-white/20">
                    {history.length === 0 ? (
                        <div className="text-white/40 text-sm italic p-4 text-center border border-dashed border-white/10 rounded-xl">
                            No recent activity
                        </div>
                    ) : (
                        history.map((item) => (
                            <div
                                key={item.id}
                                className="group flex flex-col p-4 rounded-xl hover:bg-white/5 cursor-pointer transition-all border border-transparent hover:border-white/10"
                            >
                                <div className="flex items-center justify-between mb-1">
                                    <span className="text-xs font-bold text-white/50 tracking-wider">
                                        {item.id}
                                    </span>
                                    <div className={`h-2 w-2 rounded-full ${item.status === 'success' ? 'bg-accent shadow-[0_0_8px_hsl(var(--accent))]' :
                                            item.status === 'processing' ? 'bg-primary animate-pulse' : 'bg-destructive'
                                        }`} />
                                </div>
                                <div className="text-sm font-semibold truncate text-white/90">
                                    {item.filename}
                                </div>
                                <div className="text-[10px] text-white/40 font-medium">
                                    {item.date}
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
};
