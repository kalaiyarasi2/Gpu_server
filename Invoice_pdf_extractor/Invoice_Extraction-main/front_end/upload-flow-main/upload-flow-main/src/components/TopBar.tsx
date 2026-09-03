import { Grid, ChevronRight } from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";

interface TopBarProps {
    sidebarOpen: boolean;
    onToggleSidebar: () => void;
}

export const TopBar = ({ sidebarOpen, onToggleSidebar }: TopBarProps) => {
    return (
        <div className="h-16 bg-white dark:bg-card border-b border-border shadow-sm flex items-center justify-between px-8 w-full transition-colors duration-300">
            <div className="flex items-center gap-4">
                {!sidebarOpen && (
                    <button
                        onClick={onToggleSidebar}
                        className="p-2 hover:bg-muted rounded-md transition-colors text-muted-foreground hover:text-foreground animate-in slide-in-from-left-4 duration-300"
                        title="Show History"
                    >
                        <ChevronRight size={20} />
                    </button>
                )}
                <img src="/Logo.png" alt="CogNet Logo" className="h-10 w-auto" />
            </div>

            <div className="flex items-center gap-4 text-muted-foreground">
                <ThemeToggle />
            </div>
        </div>
    );
};
