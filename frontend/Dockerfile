# 멀티 스테이지 빌드
FROM node:20-alpine AS base

# 의존성 설치 단계
FROM base AS deps
WORKDIR /app

COPY package*.json ./
RUN npm install --legacy-peer-deps

# 빌드 단계
FROM base AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# 환경 변수
ENV NEXT_TELEMETRY_DISABLED=1
# 상대 경로를 사용하도록 빈 문자열로 설정 (런타임에 nginx/rewrites 프록시 사용)
ENV NEXT_PUBLIC_API_URL=

RUN npm run build

# 실행 단계
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public

RUN mkdir .next
RUN chown nextjs:nodejs .next

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]










