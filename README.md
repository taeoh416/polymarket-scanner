# Polymarket 세상물정 스캐너

Polymarket 예측 시장의 **확률 급변 · 자금 쏠림 · 신규 화제 이슈**에서 스포츠/초단기 코인 도박/트윗 수 스팸을 제거하고, **자체 완결형 RSS 피드 + JSON Feed + 웹 대시보드**를 GitHub Actions & GitHub Pages로 매시간 자동 발행하는 스캐너입니다.

한국 IP에서 `polymarket.com`이 HTTP 451로 차단되는 환경을 고려해, **해외 GitHub 러너가 공개 Gamma API(`/events/keyset`)를 수집**하고 한국에서는 정적 GitHub Pages 피드와 **Aside 일일/조건부 긴급 루틴**으로 소비하도록 설계되었습니다.

## 구독 주소 (GitHub Pages)

- 대시보드: `https://taeoh416.github.io/polymarket-scanner/`
- 통합 RSS (추천): `https://taeoh416.github.io/polymarket-scanner/all.xml`
- 확률 급변 RSS: `https://taeoh416.github.io/polymarket-scanner/movers.xml`
- 자금 쏠림 RSS: `https://taeoh416.github.io/polymarket-scanner/surge.xml`
- 신규 화제 RSS: `https://taeoh416.github.io/polymarket-scanner/new-hot.xml`
- 매크로·테크·지정학 RSS: `https://taeoh416.github.io/polymarket-scanner/macro-tech.xml`
- Aside 아침 브리핑용 JSON: `https://taeoh416.github.io/polymarket-scanner/feed.json`
- Aside 저녁 조건부 긴급 체크 JSON: `https://taeoh416.github.io/polymarket-scanner/urgent.json`
- 헬스체크: `https://taeoh416.github.io/polymarket-scanner/health.json`

## Aside 루틴 구성
- **아침 브리핑 (매일 07:30 KST)**: [prompts/daily-briefing.md](prompts/daily-briefing.md)
- **저녁 조건부 2회차 브리핑 (매일 20:00 KST, 신규 긴급 시그널 있을 때만)**: [prompts/evening-urgent.md](prompts/evening-urgent.md)

## 로컬 테스트

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

설정값(노이즈 제외 태그/정규식, 시그널 임계치, 쿨다운 등)은 [config/scanner.json](config/scanner.json)에서 조정할 수 있습니다.
