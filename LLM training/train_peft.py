from trl import SFTTrainer
from peft import LoraConfig
from datasets import Dataset
import pandas as pd
from transformers import AutoModelForCausalLM, BitsAndBytesConfig
import torch
from datasets import load_dataset, DatasetDict
from transformers import AutoTokenizer, BitsAndBytesConfig, TrainingArguments
import re
import random
from multiprocessing import cpu_count
import torch
from trl import SFTTrainer
from peft import LoraConfig
from transformers import AutoTokenizer, AutoModelForCausalLM #?
access_token = ""

df = pd.read_csv('/home/valabek/LLM/prompt_data/LLM_prompt_train_nostrict_v0.csv')

df_dict = df.to_dict(orient='records')

dataset = Dataset.from_dict({'instruction': [item['prompt'] for item in df_dict], 'output': [item['completion'] for item in df_dict]})
dataset = dataset.train_test_split(0.3)


model_id = "meta-llama/Meta-Llama-3-8B-Instruct"

tokenizer = AutoTokenizer.from_pretrained(model_id, token=access_token)

# set pad_token_id equal to the eos_token_id if not set
if tokenizer.pad_token_id is None:
  tokenizer.pad_token_id = tokenizer.eos_token_id

# Set reasonable default for models without max length
if tokenizer.model_max_length > 100_000:
  tokenizer.model_max_length = 2048

# Set padding strategy because of warning  
tokenizer.padding_side = 'right'

# Set chat template
# DEFAULT_CHAT_TEMPLATE = "{% for message in messages %}\n{% if message['role'] == 'user' %}\n{{ '<|user|>\n' + message['content'] + eos_token }}\n{% elif message['role'] == 'system' %}\n{{ '<|system|>\n' + message['content'] + eos_token }}\n{% elif message['role'] == 'assistant' %}\n{{ '<|assistant|>\n'  + message['content'] + eos_token }}\n{% endif %}\n{% if loop.last and add_generation_prompt %}\n{{ '<|assistant|>' }}\n{% endif %}\n{% endfor %}"
# tokenizer.chat_template = DEFAULT_CHAT_TEMPLATE
print(dataset["train"])

def apply_chat_template(example, tokenizer):
    chat = [
        {"role": "system", "content": "You will be given a question/sentence in Slovak language asked by a customer. You are supposed to select the best intent option based on the question. If you are uncertain, or none of the intents describes the question, choose 'invalid'. Your response should only contain the name of the chosen option."},
        {"role": "user", "content": example['instruction'][:-150] }, #novy system content + MS prompt
        { "role": "assistant", "content": example['output'] },
    ]
    example["text"] = tokenizer.apply_chat_template(chat, tokenize=False)
    return example

column_names = list(dataset["train"].features)
print(dataset["train"])
raw_datasets = dataset.map(apply_chat_template,
                                #num_proc=cpu_count(),
                                fn_kwargs={"tokenizer": tokenizer},
                                remove_columns=column_names,
                                desc="Applying chat template",)

print(raw_datasets)
print(raw_datasets["train"])
print(raw_datasets["train"][0])
# create the splits
train_dataset = raw_datasets["train"]
eval_dataset = raw_datasets["test"]

for index in random.sample(range(len(raw_datasets["train"])), 3):
  print(f"Sample {index} of the processed training set:\n\n{raw_datasets['train'][index]['text']}")
  


# specify how to quantize the model
quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
)
device_map = {"": torch.cuda.current_device()} if torch.cuda.is_available() else None

model_kwargs = dict(
    attn_implementation="flash_attention_2", # set this to True if your GPU supports it (Flash Attention drastically speeds up model computations)
    token=access_token,
    torch_dtype="auto",
    use_cache=False, # set to False as we're going to use gradient checkpointing
    device_map="auto",
    # quantization_config=quantization_config,
)

# path where the Trainer will save its checkpoints and logs
output_dir = 'data/llama3_trained_NONquant_hyperpar'

# based on config
training_args = TrainingArguments(
    bf16=True, # specify bf16=True instead when training on GPUs that support bf16 else fp16
    do_eval=True,
    evaluation_strategy="epoch",
    gradient_accumulation_steps=16,#128
    gradient_checkpointing=True,
    gradient_checkpointing_kwargs={"use_reentrant": False},
    #warmup_steps=200,
    learning_rate=2.0e-04,
    log_level="info",
    logging_steps=5,
    logging_strategy="steps",
    lr_scheduler_type="cosine",
    max_steps=-1,
    num_train_epochs=3,
    output_dir=output_dir,
    overwrite_output_dir=True,
    per_device_eval_batch_size=1, # originally set to 8
    per_device_train_batch_size=1, # originally set to 8
    # push_to_hub=True,
    # hub_model_id="zephyr-7b-sft-lora",
    # hub_strategy="every_save",
    # report_to="tensorboard", 
    # figure out how to save best model
    save_strategy="epoch",
    save_total_limit=None,
    seed=42,
    #optim='paged_adamw_32bit',
)

# based on config
peft_config = LoraConfig(
        r=64,
        lora_alpha=128,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
)
print(train_dataset)

trainer = SFTTrainer(
        model=model_id,
        model_init_kwargs=model_kwargs,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        dataset_text_field="text",
        tokenizer=tokenizer,
        packing=True,
        peft_config=peft_config,
        max_seq_length=tokenizer.model_max_length,
    )

train_result = trainer.train()

metrics = train_result.metrics
max_train_samples = len(train_dataset) #training_args.max_train_samples if training_args.max_train_samples is not None else len(train_dataset)
metrics["train_samples"] = min(max_train_samples, len(train_dataset))
trainer.log_metrics("train", metrics)
trainer.save_metrics("train", metrics)
trainer.save_state()


