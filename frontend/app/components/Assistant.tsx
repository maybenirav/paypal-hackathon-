"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import * as api from "../api";
import type { AskResponse, Row } from "../api";
import { metricLabel } from "../format";
import { Icon } from "../icons";
import { MetricBars } from "./SmallCharts";

const SUGGESTIONS = ["churn above baseline", "best conversion", "best per visitor", "retention below 80%"];

type Props = {
  expId: string;
  rows: Row[];
  /** The timeline day on screen, so answers match the numbers beside them. Undefined means the whole run. */
  asOfDay?: number;
  colorOf: (armId: string) => string;
  onFilter: (armIds: string[] | null) => void;
  onError: (error: unknown) => void;
};

export default function Assistant({ expId, rows, asOfDay, colorOf, onFilter, onError }: Props) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [asking, setAsking] = useState(false);

  // An answer describes one moment in the test, so moving the timeline retires it.
  const answeredDay = useRef(asOfDay);
  useEffect(() => {
    if (answeredDay.current === asOfDay) return;
    answeredDay.current = asOfDay;
    setAnswer(null);
    onFilter(null);
  }, [asOfDay, onFilter]);

  async function ask(text: string) {
    if (!text.trim() || asking) return;
    setQuestion(text);
    setAsking(true);
    try {
      const response = await api.askAssistant(expId, text, asOfDay);
      setAnswer(response);
      onFilter(response.filter || response.sort ? response.rows.map((r) => r.arm_id) : null);
    } catch (error) {
      onError(error);
    } finally {
      setAsking(false);
    }
  }

  function clear() {
    setAnswer(null);
    setQuestion("");
    onFilter(null);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  const metric = answer?.chart?.metric;
  const filtered = answer?.filter || answer?.sort;

  return (
    <div className="assistant">
      <form className="assistant-form" onSubmit={submit}>
        <input className="input" value={question} onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about these options, e.g. churn above baseline" aria-label="Ask the assistant" />
        <button className="btn" disabled={asking || !question.trim()} aria-label="Ask"><Icon name="send" /></button>
      </form>
      <div className="chips">
        {SUGGESTIONS.map((s) => (
          <button key={s} type="button" className="chip small" onClick={() => void ask(s)}>{s}</button>
        ))}
      </div>
      {answer && (
        <div className="assistant-answer" role="status">
          <p>
            {answer.answer}
            {filtered && <button className="link" onClick={clear}>Clear {answer.filter ? "filter" : "sort"}</button>}
            {!filtered && <button className="link" onClick={clear}>Dismiss</button>}
          </p>
          {metric && (
            <figure>
              <figcaption>{metricLabel(metric)}</figcaption>
              <MetricBars metric={metric} rows={rows} colorOf={colorOf} />
            </figure>
          )}
        </div>
      )}
    </div>
  );
}
