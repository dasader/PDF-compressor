import type { Job } from "./api";

export type JobUpdateHandler = (partial: Partial<Job> & { job_id: string }) => void;

export function subscribeJob(
  jobId: string,
  onUpdate: JobUpdateHandler,
  onTerminal?: () => void
): () => void {
  const url = `/api/jobs/${jobId}/stream`;
  let es: EventSource | null = new EventSource(url);

  const handleSnapshot = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onUpdate({ ...data, id: data.job_id });
      if (["completed", "failed", "cancelled"].includes(data.status)) {
        es?.close();
        es = null;
        onTerminal?.();
      }
    } catch (err) {
      console.error("SSE snapshot parse error", err);
    }
  };

  const handleUpdate = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onUpdate({ ...data, id: data.job_id });
      if (data.type === "status" && ["completed", "failed", "cancelled"].includes(data.status)) {
        es?.close();
        es = null;
        onTerminal?.();
      }
    } catch (err) {
      console.error("SSE update parse error", err);
    }
  };

  const handleError = () => {
    console.warn(`SSE connection error for job ${jobId}`);
    es?.close();
    es = null;
  };

  es.addEventListener("snapshot", handleSnapshot as EventListener);
  es.addEventListener("update", handleUpdate as EventListener);
  es.addEventListener("error", handleError);

  return () => {
    es?.close();
    es = null;
  };
}
