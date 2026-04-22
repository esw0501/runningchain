# 러닝체인 (RunningChain)

거래소 쿠폰·보너스를 활용한 무위험 차익거래 시뮬레이터.

## 구조
- `index.html` — 단일 페이지 앱 (외부 CDN: Pretendard, JetBrains Mono, Chart.js)
- 외부 네트워크 필요 (폰트/차트 라이브러리 CDN 로딩)
- 데이터는 브라우저 `localStorage`에 저장 (서버 불필요)

## 로컬 테스트
```
python -m http.server 8000
# http://localhost:8000 에서 확인
```

## 배포 옵션

### 1) Vercel (추천 — 무료, 커스텀 도메인 가능)
```
npm i -g vercel
cd runningchain-deploy
vercel
```
프롬프트에서 프로젝트 이름만 입력하면 배포 URL 발급.

### 2) Netlify Drop
https://app.netlify.com/drop 에 폴더 전체를 드래그앤드롭.

### 3) GitHub Pages
이 폴더를 새 저장소로 푸시 → Settings → Pages → main branch / root 선택.

### 4) Cloudflare Pages
대시보드에서 Upload directory → runningchain-deploy 업로드.

## 배포 후 체크
- [ ] Simple 모드 3단계 계산 동작
- [ ] HEDGE 선택 시 A/B 원금·레버리지 독립 조절
- [ ] Expert 모드 계산 일치
- [ ] 라이트/다크 테마 토글
- [ ] 계획 저장 / CSV·JSON 내보내기·불러오기
- [ ] 모바일 반응형 (560px 이하)

## 주의
- 실거래 자동화 아님. 계산·시뮬레이션 전용.
- 쿠폰·보너스 정책은 거래소별로 빈번히 변경되므로 실행 전 해당 거래소 약관 확인 필요.
