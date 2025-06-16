import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

DEFAULT_SYSTEM_PROMPT = """"
        あなたは、鳥海山の登山ルートに特化した、日本語を話す親切でプロフェッショナルな登山ガイドAIです。

        雑談対応（以下のようなときはツールを使わず、自然に返してください）：
        - 「こんにちは」「調子はどう？」
        - 「天気がいいですね」
        - 「最近どうですか？」
        - 軽い世間話、あいさつ

        雑談のときはフレンドリーな日本語で丁寧に応答してください。
    """
text = "Hello"

model_name = "elyza/Llama-3-ELYZA-JP-8B"

tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype="auto",
    device_map="auto",
)
model.eval()

messages = [
    {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
    {"role": "user", "content": text},
]
prompt = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True
)
token_ids = tokenizer.encode(
    prompt, add_special_tokens=False, return_tensors="pt"
)

with torch.no_grad():
    output_ids = model.generate(
        token_ids.to(model.device),
        max_new_tokens=1200,
        do_sample=True,
        temperature=0.6,
        top_p=0.9,
    )
output = tokenizer.decode(
    output_ids.tolist()[0][token_ids.size(1):], skip_special_tokens=True
)
print(output)
