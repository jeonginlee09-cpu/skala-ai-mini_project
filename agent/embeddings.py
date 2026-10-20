"""Explicit OpenAI embeddings; keys never enter persisted index metadata."""
import os

import httpx


class OpenAIEmbedding:
    def __init__(self,model=None):
        self.model=model or os.environ.get('OPENAI_EMBEDDING_MODEL','text-embedding-3-small')

    def embed(self,texts):
        key=os.environ.get('OPENAI_API_KEY')
        if not key:raise RuntimeError('OPENAI_API_KEY is required for embeddings')
        # https://developers.openai.com/api/reference/resources/embeddings/methods/create
        response=httpx.post('https://api.openai.com/v1/embeddings',
            headers={'Authorization':'Bearer '+key},
            json={'model':self.model,'input':texts,'encoding_format':'float'},timeout=60)
        response.raise_for_status();data=response.json()
        rows=sorted(data['data'],key=lambda row:row['index'])
        if len(rows)!=len(texts):raise ValueError('embedding_count_mismatch')
        return [row['embedding'] for row in rows],{
            'tokens':data.get('usage',{}).get('total_tokens',0),'requests':1,'mode':'openai'}
