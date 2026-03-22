
# Aider Support

## Project Path 

cd C:\Users\ritte\_PR_DEV_\DEV_PYTHON\ITALIAN_CHAT_CLI

## Set Key

setx DEEPSEEK_API_KEY your_deepseek_api_key
echo %DEEPSEEK_API_KEY%


# Command line to launch model

## Deep Seek (Direct)

 aider --deepseek --api-key deepseek=sk-8b25863908ee4905bfcf2ba55361aae8

aider --deepseek
aider --deepseek --api-key deepseek=xxxxxxxxxxxx
aider --architect --model  deepseek/deepseek-r1  --editor-model deepseek/deepseek-chat deepseek=xxxxxxxxxxxxxxxxxxx

## Open Router

free gemini:

aider --model  openrouter/google/gemini-2.5-pro-preview-03-25 --edit-format diff-fenced

aider --model  openrouter/google/gemini-2.5-pro-exp-03-25:free  --edit-format diff

aider --model  openrouter/google/gemini-2.5-pro-preview-03-25  --edit-format diff
aider --model  openrouter/google/gemini-2.5-pro-exp-03-25:free  --edit-format diff

aider --model  openrouter/deepseek/deepseek-chat

aider --model  openrouter/deepseek/deepseek-r1

aider --model  openrouter/anthropic/claude-3.7-sonnet
aider --model  openrouter/anthropic/claude-3.5-sonnet:beta


aider --architect --model  openrouter/deepseek/deepseek-r1  --editor-model openrouter/deepseek/deepseek-chat


aider --model  openrouter/google/gemini-2.0-flash-exp:free --edit-format diff

## XAI

key: xai-Vo0iEcsvcDze0MOLCFQikes9UBzoCk84qvGuKPGxLtYadMfKMhxOcOtmUgJ9CM76Wmb025WUXtIjOUiU
windows: setx XAI_API_KEY <your_xai_api_key>

aider --model xai/grok-beta --edit-format diff