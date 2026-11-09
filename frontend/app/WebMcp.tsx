"use client";

// Exposes PlanLab's read-only agent tools to browser agents through WebMCP
// (navigator.modelContext). Browsers without WebMCP skip this silently.
import { useEffect } from "react";
import { listTools, runTool } from "./api-insights";

type ToolResult = { content: { type: "text"; text: string }[] };
type WebMcpTool = {
  name: string; description: string; inputSchema: Record<string, unknown>;
  annotations?: { readOnlyHint?: boolean };
  execute: (args: Record<string, unknown>) => Promise<ToolResult>;
};
type ModelContext = {
  provideContext?: (ctx: { tools: WebMcpTool[] }) => void;
  registerTool?: (tool: WebMcpTool) => void;
};

export default function WebMcp() {
  useEffect(() => {
    const mc = (navigator as unknown as { modelContext?: ModelContext }).modelContext;
    if (!mc) return;
    listTools().then(({ tools }) => {
      const webTools: WebMcpTool[] = tools.map((t) => ({
        name: `planlab_${t.name}`, description: t.description, inputSchema: t.inputSchema,
        annotations: { readOnlyHint: true },
        execute: async (args) => {
          const { result } = await runTool(t.name, args ?? {});
          return { content: [{ type: "text", text: JSON.stringify(result) }] };
        },
      }));
      if (mc.provideContext) mc.provideContext({ tools: webTools });
      else webTools.forEach((t) => mc.registerTool?.(t));
    }).catch(() => { /* backend down: no tools */ });
  }, []);
  return null;
}
