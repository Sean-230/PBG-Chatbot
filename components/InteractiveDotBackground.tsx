"use client";

import { useEffect, useRef, useState } from "react";
import { useTheme } from "next-themes";

interface Dot {
  x: number;
  y: number;
  originX: number;
  originY: number;
  color: string;
}

export default function InteractiveDotBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mounted, setMounted] = useState(false);
  const { theme } = useTheme();

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animationFrameId: number;
    let dots: Dot[] = [];
    const spacing = 35; // Spacing between dots
    const radius = 150; // Mouse repulsion radius
    const pushMagnitude = 30; // How far the dot is pushed

    // Track mouse position (-1000 to keep it offscreen initially)
    let mouseX = -1000;
    let mouseY = -1000;

    const isDark = theme === "dark" || theme === "system"; // Default to dark for system if needed, though useTheme usually resolves this

    // Generate grid
    const initGrid = () => {
      // Set canvas dimensions to window inner size
      // Multiply by devicePixelRatio for sharp rendering on retina displays
      const dpr = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      
      ctx.scale(dpr, dpr);
      
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;

      dots = [];
      const cols = Math.floor(window.innerWidth / spacing);
      const rows = Math.floor(window.innerHeight / spacing);

      // Center the grid
      const offsetX = (window.innerWidth - cols * spacing) / 2;
      const offsetY = (window.innerHeight - rows * spacing) / 2;

      for (let i = 0; i <= cols; i++) {
        for (let j = 0; j <= rows; j++) {
          dots.push({
            x: offsetX + i * spacing,
            y: offsetY + j * spacing,
            originX: offsetX + i * spacing,
            originY: offsetY + j * spacing,
            color: isDark ? "rgba(255, 255, 255, 0.15)" : "rgba(0, 0, 0, 0.15)",
          });
        }
      }
    };

    const handleResize = () => {
      initGrid();
    };

    const handleMouseMove = (e: MouseEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
    };
    
    const handleMouseLeave = () => {
      mouseX = -1000;
      mouseY = -1000;
    };

    window.addEventListener("resize", handleResize);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseleave", handleMouseLeave);

    // Initial grid setup
    initGrid();

    // Animation Loop
    const render = () => {
      // Clear canvas
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // Draw soft glow behind dots (canvas approach)
      if (mouseX > -1000) {
        const gradient = ctx.createRadialGradient(
          mouseX, mouseY, 0,
          mouseX, mouseY, 600
        );
        
        if (isDark) {
          gradient.addColorStop(0, "rgba(192, 132, 252, 0.04)"); // Soft purple
          gradient.addColorStop(1, "transparent");
        } else {
          gradient.addColorStop(0, "rgba(99, 102, 241, 0.04)"); // Soft indigo
          gradient.addColorStop(1, "transparent");
        }
        
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, window.innerWidth, window.innerHeight);
      }

      // Physics and rendering for dots
      for (let i = 0; i < dots.length; i++) {
        const dot = dots[i];
        
        let targetX = dot.originX;
        let targetY = dot.originY;

        // Calculate distance from mouse
        const dx = mouseX - dot.originX;
        const dy = mouseY - dot.originY;
        const dist = Math.sqrt(dx * dx + dy * dy);

        // Repel logic
        if (dist < radius) {
          const force = (radius - dist) / radius;
          targetX -= (dx / dist) * force * pushMagnitude;
          targetY -= (dy / dist) * force * pushMagnitude;
        }

        // Spring logic (lerp towards target)
        dot.x += (targetX - dot.x) * 0.15;
        dot.y += (targetY - dot.y) * 0.15;

        // Draw dot
        ctx.beginPath();
        ctx.arc(dot.x, dot.y, 1, 0, Math.PI * 2);
        ctx.fillStyle = dot.color;
        ctx.fill();
      }

      animationFrameId = requestAnimationFrame(render);
    };

    render();

    // Cleanup
    return () => {
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseleave", handleMouseLeave);
      cancelAnimationFrame(animationFrameId);
    };
  }, [mounted, theme]);

  if (!mounted) return null;

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 z-0 pointer-events-none"
    />
  );
}
