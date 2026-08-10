import multiprocessing

# 1. 서버 바인딩 주소 및 포트 (0.0.0.0은 외부 모든 IP 접근 허용)
bind = "0.0.0.0:8000"

# 2. 자원 및 워커 설정
# 일반적으로 (CPU 코어 수 * 2) + 1 공식을 사용합니다. 
# 학교 서버가 4코어라면 총 9개의 워커 프로세스가 생성됩니다.
# workers = (multiprocessing.cpu_count() * 2) + 1
workers = 1

# 3. 핵심: 비동기 처리를 위해 Uvicorn 워커 클래스를 지정합니다.
worker_class = "uvicorn.workers.UvicornWorker"

# 4. 프로세스 관리 및 안정성 설정
backlog = 2048          # 대기 가능한 최대 커넥션 수
timeout = 300           # 스트리밍 채팅을 위해 워커 타임아웃 상향
keepalive = 2           # HTTP Keep-Alive 시간 설정 (성능 향상)

# 5. 로깅 설정 (서버 터미널이나 파일에서 확인 가능)
accesslog = "-"          # stdout(터미널)에 액세스 로그 출력
errorlog = "-"           # stdout(터미널)에 에러 로그 출력
loglevel = "info"