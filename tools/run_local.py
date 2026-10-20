"""Run this copy on dedicated localhost ports without killing servers or deleting sessions."""
import argparse
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
import uvicorn

parser=argparse.ArgumentParser()
parser.add_argument('role',choices=['backend','frontend'])
parser.add_argument('--model-backend',choices=['openai','ollama','fake'],default='openai')
parser.add_argument('--port',type=int)
parser.add_argument('--backend-url',default='http://127.0.0.1:18110')
args=parser.parse_args()
load_dotenv(ROOT/'.env')
if args.role=='backend':
    os.environ['LLM_BACKEND']=args.model_backend
    os.environ['SUPERVISOR_VERSION']='v2'
    os.environ['LANGCHAIN_PROJECT']='capstone-step3-'+args.model_backend
    if args.model_backend=='openai' and not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('OPENAI_API_KEY를 .env 또는 환경변수에 설정하세요. 키는 출력하지 않습니다.')
    uvicorn.run('server:app',host='127.0.0.1',port=args.port or 18110,log_level='info')
else:
    os.environ['BACKEND_URL']=args.backend_url
    uvicorn.run('web_demo_server:app',host='127.0.0.1',port=args.port or 18610,log_level='info')
