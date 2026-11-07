"use client";

import { useMemo } from "react";
import { BryntumGantt } from "@bryntum/gantt-react";
import type { Timeline as TimelineData } from "./api";
import "@bryntum/gantt/gantt.css";
import "@bryntum/gantt/stockholm-dark.css";
import "@bryntum/gantt/fontawesome/css/fontawesome.css";
import "@bryntum/gantt/fontawesome/css/solid.css";

/** Experiment plan from GET /experiments/{id}/timeline: launch, guardrail checkpoints, decision, rollout. */
export default function Timeline({ data }: { data: TimelineData }) {
  const project = useMemo(() => ({
    startDate: data.project.startDate,
    tasksData: data.tasks.rows,
    dependenciesData: data.dependencies.rows,
  }), [data]);

  return (
    <div className="gantt-wrap">
      <BryntumGantt
        project={project}
        readOnly
        columns={[{ type: "name", field: "name", text: "Step", width: 260 }]}
        viewPreset="weekAndMonth"
        barMargin={6}
      />
    </div>
  );
}
