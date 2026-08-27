import { TERMINAL_STATUSES, type Job } from "./api";

export type JobUpdateHandler = (partial: Partial<Job> & { job_id: string }) => void;

export function subscribeJob(
  jobId: string,
  onUpdate: JobUpdateHandler,
  onTerminal?: () => void
): () => void {
  let es: EventSource | null = new EventSource(`/api/jobs/${jobId}/stream`);

  const close = () => {
    es?.close();
    es = null;
  };

  // snapshot은 status 필드가 그대로 상태이고, update는 type === "status" 이벤트만 상태 전이다
  const handle = (requireStatusType: boolean) => (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onUpdate({ ...data, id: data.job_id });

      const isStatusEvent = !requireStatusType || data.type === "status";
      if (isStatusEvent && TERMINAL_STATUSES.includes(data.status)) {
        close();
        onTerminal?.();
      }
    } catch (err) {
      console.error(`SSE parse error for job ${jobId}`, err);
    }
  };

  es.addEventListener("snapshot", handle(false) as EventListener);
  es.addEventListener("update", handle(true) as EventListener);
  es.addEventListener("error", () => {
    console.warn(`SSE connection error for job ${jobId}`);
    close();
  });

  return close;
}
