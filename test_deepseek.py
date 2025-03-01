import requests

url = "https://api.siliconflow.cn/v1/images/generations"

payload = {
    "model": "Kwai-Kolors/Kolors",
    "prompt": "1",
    "image_size": "1024x1024",
    "batch_size": 1,
    "num_inference_steps": 1,
    "guidance_scale": 1,
    "negative_prompt": "1",
    "seed": 1
}
headers = {
    "Authorization": "Bearer sk-oitgzcauugwywxcmjcglritmcluknzboixetemwugzeojndm",
    "Content-Type": "application/json"
}

response = requests.request("POST", url, json=payload, headers=headers)

print(response.text)