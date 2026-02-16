import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";

export const ThemeToggle = () => {
    const [isDark, setIsDark] = useState(false);

    useEffect(() => {
        const savedTheme = localStorage.getItem("invoice-ai-react-theme");
        const systemPrefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;

        const initialIsDark = savedTheme === "dark" || (!savedTheme && systemPrefersDark);
        setIsDark(initialIsDark);

        if (initialIsDark) {
            document.documentElement.classList.add("dark");
        } else {
            document.documentElement.classList.remove("dark");
        }
    }, []);

    const toggleTheme = () => {
        const newIsDark = !isDark;
        setIsDark(newIsDark);

        if (newIsDark) {
            document.documentElement.classList.add("dark");
            localStorage.setItem("invoice-ai-react-theme", "dark");
        } else {
            document.documentElement.classList.remove("dark");
            localStorage.setItem("invoice-ai-react-theme", "light");
        }
    };

    return (
        <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            className="w-10 h-10 rounded-full hover:bg-muted transition-all duration-300 relative group overflow-hidden"
        >
            <div className="relative w-full h-full flex items-center justify-center">
                <Sun className={`absolute h-5 w-5 transition-all duration-500 ${!isDark ? 'rotate-0 scale-100 opacity-100' : 'rotate-90 scale-0 opacity-0'}`} />
                <Moon className={`absolute h-5 w-5 transition-all duration-500 ${isDark ? 'rotate-0 scale-100 opacity-100' : '-rotate-90 scale-0 opacity-0'}`} />
            </div>
            <span className="sr-only">Toggle theme</span>
        </Button>
    );
};
