import { isTerminal } from "./constants";
import type { Job } from "./api";

export type JobUpdateHandler = (partial: Partial<Job> & { job_id: string }) => void;

/** 재연결을 몇 번까지 브라우저에 맡길지 — 넘으면 포기한다 (예: job이 삭제된 경우 무한 재시도 방지) */
const MAX_RECONNECTS = 5;

export function subscribeJob(
  jobId: string,
  onUpdate: JobUpdateHandler,
  onTerminal?: () => void
): () => void {
  let es: EventSource | null = new EventSource(`/api/jobs/${jobId}/stream`);
  let errors = 0;

  const close = () => {
    es?.close();
    es = null;
  };

  // snapshot은 status 필드가 그대로 상태이고, update는 type === "status" 이벤트만 상태 전이다
  const handle = (requireStatusType: boolean) => (e: MessageEvent) => {
    errors = 0;
    try {
      const data = JSON.parse(e.data);
      onUpdate({ ...data, id: data.job_id });

      const isStatusEvent = !requireStatusType || data.type === "status";
      if (isStatusEvent && isTerminal(data.status)) {
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
    // close()를 호출하지 않으면 브라우저가 알아서 재연결하고, 백엔드는 접속 즉시 snapshot을 다시 보낸다.
    // 다만 job이 사라진 경우엔 영원히 재시도하므로 횟수를 제한한다.
    if (++errors > MAX_RECONNECTS) {
      console.warn(`SSE 재연결 ${MAX_RECONNECTS}회 실패, 구독 종료: job ${jobId}`);
      close();
    }
  });

  return close;
}
