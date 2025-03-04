# -*- coding: utf-8 -*-

import os
import re
import time

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from videotrans.configure import config
from videotrans.util import tools

safetySettings = [
    {
        "category": HarmCategory.HARM_CATEGORY_HARASSMENT,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
    {
        "category": HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
    {
        "category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
    {
        "category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        "threshold": HarmBlockThreshold.BLOCK_NONE,
    },
]

shound_del = False


def update_proxy(type='set'):
    global shound_del
    if type == 'del' and shound_del:
        del os.environ['http_proxy']
        del os.environ['https_proxy']
        del os.environ['all_proxy']
        shound_del = False
    elif type == 'set':
        raw_proxy = os.environ.get('http_proxy')
        if not raw_proxy:
            proxy = tools.set_proxy()
            if proxy:
                shound_del = True
                os.environ['http_proxy'] = proxy
                os.environ['https_proxy'] = proxy
                os.environ['all_proxy'] = proxy


def get_error(num=5, type='error'):
    REASON_CN = {
        2: "超出长度",
        3: "安全限制",
        4: "文字过度重复",
        5: "其他原因"
    }
    REASON_EN = {
        2: "The maximum number of tokens as specified",
        3: "The candidate content was flagged for safety",
        4: "The candidate content was flagged",
        5: "Unknown reason"
    }
    forbid_cn = {
        1: "被Gemini禁止翻译:出于安全考虑，提示已被屏蔽",
        2: "被Gemini禁止翻译:由于未知原因，提示已被屏蔽"
    }
    forbid_en = {
        1: "Translation banned by Gemini:for security reasons, the prompt has been blocked",
        2: "Translation banned by Gemini:prompt has been blocked for unknown reasons"
    }
    if config.defaulelang == 'zh':
        return REASON_CN[num] if type == 'error' else forbid_cn[num]
    return REASON_EN[num] if type == 'error' else forbid_en[num]




def trans(text_list, target_language="English", *, set_p=True, inst=None, stop=0, source_code="", is_test=False,uuid=None):
    def get_content(d, *, model=None, prompt=None):
        update_proxy(type='set')
        response = None
        try:
            if '{text}' in prompt:
                message = prompt.replace('{text}', "\n".join([i.strip() for i in d]) if isinstance(d, list) else d)
            else:
                message = prompt.replace('[TEXT]', "\n".join([i.strip() for i in d]) if isinstance(d, list) else d)
            response = model.generate_content(
                message
            )
            config.logger.info(f'[Gemini]请求发送:{message=}')

            result = response.text.replace('##', '').strip().replace('&#39;', '"').replace('&quot;', "'")
            config.logger.info(f'[Gemini]返回:{result=}')
            if not result:
                raise Exception("fail")
            return re.sub(r'\n{2,}', "\n", result)
        except Exception as e:
            error = str(e)
            if set_p:
                tools.set_process(
                    error,
                    type="logs",
                    btnkey=inst.init['btnkey'] if inst else "",
                    uuid=uuid
                )
            config.logger.error(f'[Gemini]请求失败:{error=}')
            if response and response.prompt_feedback.block_reason:
                raise Exception(get_error(response.prompt_feedback.block_reason, "forbid"))

            if error.find('User location is not supported') > -1 or error.find('time out') > -1:
                raise Exception("当前请求ip(或代理服务器)所在国家不在Gemini API允许范围")

            if response and len(response.candidates) > 0 and response.candidates[0].finish_reason not in [0, 1]:
                raise Exception(get_error(response.candidates[0].finish_reason))

            if response and len(response.candidates) > 0 and response.candidates[0].finish_reason == 1 and \
                    response.candidates[0].content and response.candidates[0].content.parts:
                result = response.text.replace('##', '').strip().replace('&#39;', '"').replace('&quot;', "'")
                return re.sub(r'\n{2,}', "\n", result)
            raise

    """
    text_list:
        可能是多行字符串，也可能是格式化后的字幕对象数组
    target_language:
        目标语言
    set_p:
        是否实时输出日志，主界面中需要
    """
    wait_sec = 0.5
    try:
        wait_sec = int(config.settings['translation_wait'])
    except Exception:
        pass
    try:
        print(f'########{set_p=},{uuid=}')
        if set_p:
            tools.set_process(
                f'Connecting Gemini API' ,
                type="logs",
                btnkey=inst.init['btnkey'] if inst else "",
                uuid=uuid
            )
        genai.configure(api_key=config.params['gemini_key'])
        model = genai.GenerativeModel(config.params['gemini_model'], safety_settings=safetySettings)
    except Exception as e:
        err = str(e)
        print(f'%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%{err},{set_p=},{uuid=}')
        if set_p:
            tools.set_process(
                err,
                type="error",
                btnkey=inst.init['btnkey'] if inst else "",
                uuid=uuid
            )
        raise Exception(f'请正确设置http代理,{err}')

    # 翻译后的文本
    target_text = {"0": [], "srts": []}
    index = -1  # 当前循环需要开始的 i 数字,小于index的则跳过
    iter_num = 0  # 当前循环次数，如果 大于 config.settings.retries 出错
    err = ""
    is_srt = False if isinstance(text_list, str) else True
    split_size = int(config.settings['trans_thread'])

    prompt = config.params['gemini_template'].replace('{lang}', target_language)

    # 切割为每次翻译多少行，值在 set.ini中设定，默认10
    end_point = "。" if config.defaulelang == 'zh' else ' . '
    # 整理待翻译的文字为 List[str]
    if not is_srt:
        source_text = [t.strip() for t in text_list.strip().split("\n") if t.strip()]
    else:
        source_text = []
        for i, it in enumerate(text_list):
            source_text.append(it['text'].strip().replace('\n', '.') + end_point)
    split_source_text = [source_text[i:i + split_size] for i in range(0, len(source_text), split_size)]

    while 1:
        if config.exit_soft or (config.current_status != 'ing' and config.box_trans != 'ing' and not is_test):
            return

        if iter_num > int(config.settings['retries']):
            err = f'{iter_num}{"次重试后依然出错" if config.defaulelang == "zh" else " retries after error persists "}:{err}'
            break

        if iter_num >= 1:
            if set_p:
                tools.set_process(
                    f"第{iter_num}次出错重试" if config.defaulelang == 'zh' else f'{iter_num} retries after error',
                    type="logs",
                    btnkey=inst.init['btnkey'] if inst else "",
                    uuid=uuid)
            time.sleep(10)
        iter_num += 1

        for i, it in enumerate(split_source_text):
            if config.exit_soft or (config.current_status != 'ing' and config.box_trans != 'ing' and not is_test):
                return
            if i <= index:
                continue
            if stop > 0:
                time.sleep(stop)
            try:
                result = get_content(it, model=model, prompt=prompt)
                if inst and inst.precent < 75:
                    inst.precent += 0.01
                if not is_srt:
                    target_text["0"].append(result)
                    continue

                sep_res = tools.cleartext(result).split("\n")
                raw_len = len(it)
                sep_len = len(sep_res)
                # 如果返回结果相差原字幕仅少一行，对最后一行进行拆分
                if sep_len + 1 == raw_len:
                    config.logger.error('如果返回结果相差原字幕仅少一行，对最后一行进行拆分')
                    sep_res = tools.split_line(sep_res)
                    if sep_res:
                        sep_len = len(sep_res)

                # 如果返回数量和原始语言数量不一致，则重新切割
                if sep_len < raw_len:
                    config.logger.error(f'翻译前后数量不一致，需要重新按行翻译')
                    sep_res = []
                    for line_res in it:
                        time.sleep(wait_sec)
                        sep_res.append(get_content(line_res.strip(), model=model, prompt=prompt))
            except Exception as e:
                err = str(e)
                time.sleep(wait_sec)
                config.logger.error(f'翻译出错:暂停{wait_sec}s')
                break
            else:
                # 未出错
                config.logger.info(f'{sep_res=}\n{it=}')
                for x, result_item in enumerate(sep_res):
                    if x < len(it):
                        target_text["srts"].append(result_item.strip().rstrip(end_point))
                        if set_p:
                            tools.set_process(
                                result_item + "\n",
                                type='subtitle',
                                uuid=uuid)
                            tools.set_process(
                                config.transobj['starttrans'] + f' {i * split_size + x + 1} ',
                                type="logs",
                                btnkey=inst.init['btnkey'] if inst else "",
                                uuid=uuid)
                if len(sep_res) < len(it):
                    tmp = ["" for x in range(len(it) - len(sep_res))]
                    target_text["srts"] += tmp
                err = ''
                iter_num = 0
                index = i
        else:
            break
    update_proxy(type='del')

    if err:
        config.logger.error(f'[Gemini]翻译请求失败:{err=}')
        if err.lower().find("Connection error") > -1:
            err = '连接失败 ' + err
        raise Exception(f'Gemini:{err}')

    if not is_srt:
        return "\n".join(target_text["0"])

    if len(target_text['srts']) < len(text_list) / 2:
        raise Exception(f'Gemini:{config.transobj["fanyicuowu2"]}')
    config.logger.info(f'{text_list=}\n{target_text["srts"]}')
    for i, it in enumerate(text_list):
        if i < len(target_text['srts']):
            text_list[i]['text'] = target_text['srts'][i]
        else:
            text_list[i]['text'] = ""
    return text_list
