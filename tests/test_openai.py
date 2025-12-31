from openai import AsyncAzureOpenAI
from typing import List, Dict, Any
import asyncio

from src.config import Config

async def test_openai(messages: List[Dict[str, Any]]):
    if Config.AZURE_OPENAI_ENDPOINT:
        client = AsyncAzureOpenAI(
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_KEY,
            api_version=Config.AZURE_OPENAI_VERSION
        )
        
        model_name = Config.AZURE_OPENAI_DEPLOYMENT
        
        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=8192,
            temperature=0.9,
            # tools=self.get_tools(),
            # tool_choice="auto",
            stream=True,
            stream_options={"include_usage": True}
        )
        
        async for chunk in stream:
            print(chunk)
            
            
# ChatCompletionChunk(id='chatcmpl-CffEfVlOopfFQYKE1KAeshQK3r5GD', choices=[Choice(delta=ChoiceDelta(content=None, function_call=None, refusal=None, role=None, tool_calls=None), finish_reason='stop', index=0, logprobs=None, content_filter_results={})], created=1764045993, model='gpt-4.1-mini-2024-07-18', object='chat.completion.chunk', service_tier=None, system_fingerprint='fp_efad92c60b', usage=None, obfuscation='1wxYcRHnCvmZNf')
# ChatCompletionChunk(id='chatcmpl-CffEfVlOopfFQYKE1KAeshQK3r5GD', choices=[], created=1764045993, model='gpt-4.1-mini-2024-07-18', object='chat.completion.chunk', service_tier=None, system_fingerprint='fp_efad92c60b', usage=CompletionUsage(completion_tokens=851, prompt_tokens=14, total_tokens=865, completion_tokens_details=CompletionTokensDetails(accepted_prediction_tokens=0, audio_tokens=0, reasoning_tokens=0, rejected_prediction_tokens=0), prompt_tokens_details=PromptTokensDetails(audio_tokens=0, cached_tokens=0)), obfuscation='F')
            
            
if __name__ == "__main__":
    messages = [
        {"role": "user", "content": "Can you explain about solar system!"}
    ]
    asyncio.run(test_openai(messages))
