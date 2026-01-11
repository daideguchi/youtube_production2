from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Dict, List, Tuple

from .sot import Status


_DEFAULT_CHAPTER_TITLES_8: List[str] = [
    "導入フック",
    "日常の具体シーン",
    "問題の正体",
    "ブッダの見立て",
    "史実エピソード",
    "実践（3ステップ）",
    "落とし穴と回避",
    "締め",
]

_CHANNEL_DEFAULTS: Dict[str, Dict[str, List[str]]] = {
    "CH12": {
        "concepts": ["無常", "放下", "呼吸", "慈悲"],
        "scenes": ["夜の寝室", "ひとりの食卓", "病院帰り", "明日の予定が重い夜"],
    },
    "CH13": {
        "concepts": ["慈悲", "正語", "手放し", "距離"],
        "scenes": ["介護の現場", "食卓", "親戚の集まり", "近所づきあい"],
    },
    "CH14": {
        "concepts": ["無常", "放下", "因果", "慈悲"],
        "scenes": ["夜の反芻", "同級生の話", "片づけ", "年金不安の夜"],
    },
    "CH15": {
        "concepts": ["精進", "中道", "呼吸", "慈悲"],
        "scenes": ["朝が重い", "夜の反省", "体力低下", "休めない罪悪感"],
    },
    "CH16": {
        "concepts": ["足るを知る", "因果", "中道", "無常"],
        "scenes": ["年金不安", "病院", "家の片づけ", "朝の支度"],
    },
}

_CONCEPT_EXPLANATIONS: Dict[str, str] = {
    "無常": "ずっと同じ状態は続かない、という見方です。",
    "放下": "握りしめたものを、少し緩めて手放すことです。",
    "呼吸": "息は、心を今に戻す一番短い道です。",
    "慈悲": "自分にも相手にも、必要以上に厳しくしない姿勢です。",
    "正語": "刺さりにくい言い方を選び、争いを増やさないことです。",
    "距離": "愛があっても、距離が必要なときがあります。",
    "手放し": "全部背負わず、役割と感情を分けて置くことです。",
    "因果": "今日の小さな行いが、明日の心を作るという見方です。",
    "精進": "気合いではなく、続く形で積み重ねることです。",
    "中道": "極端に振れた心を、真ん中に戻すことです。",
    "足るを知る": "足りない所だけでなく、今あるものにも目を向けることです。",
}

_CATEGORY_BY_TAG: Dict[str, str] = {
    # Night/anxiety
    "不眠": "night",
    "睡眠": "night",
    "不安": "night",
    "心配": "night",
    "孤独": "night",
    # Regret/attachment
    "後悔": "past",
    "過去": "past",
    "執着": "past",
    "恨み": "past",
    "寂しさ": "past",
    "比較": "past",
    "自己否定": "past",
    # Anger/resilience
    "怒り": "anger",
    "完璧": "anger",
    "心": "anger",
    "回復": "anger",
    "習慣": "anger",
    "継続": "anger",
    "先延ばし": "anger",
    # Relationship
    "家族": "relationship",
    "夫婦": "relationship",
    "子ども": "relationship",
    "介護": "relationship",
    "親戚": "relationship",
    "近所": "relationship",
    "悪口": "relationship",
    "頼み": "relationship",
    # Money/life
    "老後不安": "money",
    "節約": "money",
    "固定費": "money",
    "浪費": "money",
    "年金": "money",
    "見栄": "money",
    "運": "money",
    "因果": "money",
}


def _seed_int(text: str) -> int:
    h = hashlib.sha1((text or "").encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _rng_for(st: Status, *, salt: str = "") -> random.Random:
    title = str(st.metadata.get("title") or st.script_id or "")
    base = f"{st.channel}-{st.video}-{title}"
    return random.Random(_seed_int(base + "|" + salt))


def _extract_title_tag(title: str) -> str:
    m = re.search(r"【([^】]+)】", title or "")
    return (m.group(1) if m else "").strip()


def _persona_chapter_titles(persona_text: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    if not persona_text:
        return out
    pat = re.compile(r"^[-*]\s*第(\d+)章[:：]\s*(.+?)\s*$")
    for line in persona_text.splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        try:
            num = int(m.group(1))
        except Exception:
            continue
        title = m.group(2).strip()
        if title:
            out[num] = title
    return out


def _chapter_titles(st: Status, chapter_count: int) -> List[str]:
    persona_map = _persona_chapter_titles(str(st.metadata.get("persona") or ""))
    titles: List[str] = []
    for i in range(1, chapter_count + 1):
        if i in persona_map:
            titles.append(persona_map[i])
            continue
        if chapter_count == 8 and 1 <= i <= 8:
            titles.append(_DEFAULT_CHAPTER_TITLES_8[i - 1])
            continue
        titles.append(f"第{i}章")
    return titles


def _channel_pick(st: Status, key: str, fallback: List[str]) -> str:
    items = _CHANNEL_DEFAULTS.get(str(st.channel).upper(), {}).get(key) or fallback
    rng = _rng_for(st, salt=f"channel:{key}")
    return rng.choice(items) if items else ""


def _tag_category(tag: str) -> str:
    return _CATEGORY_BY_TAG.get((tag or "").strip(), "")


def generate_outline_offline(base: Path, st: Status) -> List[Tuple[int, str]]:
    chapter_count = 8
    try:
        chapter_count = int(st.metadata.get("chapter_count") or 8)
    except Exception:
        chapter_count = 8

    titles = _chapter_titles(st, chapter_count)
    tag = str(st.metadata.get("main_tag") or _extract_title_tag(str(st.metadata.get("title") or "")) or "悩み").strip()
    benefit = str(st.metadata.get("benefit") or "").strip()
    scene = str(st.metadata.get("life_scene") or _channel_pick(st, "scenes", ["日常の場面"])).strip()
    concept = str(st.metadata.get("key_concept") or _channel_pick(st, "concepts", ["無常"])).strip()
    concept_expl = _CONCEPT_EXPLANATIONS.get(concept, "")

    lines: List[str] = ["# アウトライン", ""]
    for idx, title in enumerate(titles, start=1):
        lines.append(f"## 第{idx}章 {title}")
        if idx == 1:
            lines.append(f"- {tag}の苦しさに共感し、結論を先に伝える")
            if benefit:
                lines.append(f"- 視聴後に{benefit}へ近づく道筋を予告する")
        elif idx == 2:
            lines.append(f"- {scene}の具体描写で、耳に刺さる状況を作る")
        elif idx == 3:
            lines.append("- 原因を一つに絞り、頭の中で起きていることを言語化する")
        elif idx == 4:
            hint = f"- {concept}を日常語に訳し、安心の見取り図にする"
            if concept_expl:
                hint = f"- {concept}を日常語に訳す（{concept_expl}）"
            lines.append(hint)
        elif idx == 5:
            lines.append("- 短い史実エピソードで腹落ちさせる")
        elif idx == 6:
            lines.append("- 今夜からできる三つの手順に落とす")
        elif idx == 7:
            lines.append("- よくある誤解と逆効果を先に潰す")
        else:
            lines.append("- 一言で背中を押し、静かに終える")
        lines.append("")

    out_path = base / "content" / "outline.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return [(i, titles[i - 1]) for i in range(1, chapter_count + 1)]


def generate_chapter_briefs_offline(base: Path, st: Status, chapters: List[Tuple[int, str]]) -> None:
    tag = str(st.metadata.get("main_tag") or _extract_title_tag(str(st.metadata.get("title") or "")) or "悩み").strip()
    concept = str(st.metadata.get("key_concept") or _channel_pick(st, "concepts", ["無常"])).strip()
    scene = str(st.metadata.get("life_scene") or _channel_pick(st, "scenes", ["日常の場面"])).strip()
    benefit = str(st.metadata.get("benefit") or "").strip()

    briefs: List[Dict[str, object]] = []
    for num, heading in chapters:
        rng = _rng_for(st, salt=f"brief:{num}")
        focus = rng.choice(
            [
                "共感を深める",
                "原因を一つに絞る",
                "言い換えで安心させる",
                "具体行動に落とす",
                "誤解を潰す",
            ]
        )
        briefs.append(
            {
                "chapter": num,
                "heading": heading,
                "topic": tag,
                "scene": scene,
                "concept": concept,
                "benefit": benefit,
                "focus": focus,
            }
        )

    out_path = base / "content" / "chapters" / "chapter_briefs.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(briefs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _paragraphs_for_category(category: str, *, tag: str, scene: str) -> Dict[str, List[str]]:
    if category == "relationship":
        return {
            "scene": [
                f"{scene}で、相手の一言に飲み込まれてしまう。",
                f"{scene}で、言い返せなかった後にぐったりする。",
                f"{scene}で、正しさより疲れだけが残る。",
            ],
            "cause": [
                "原因は、相手を変える前に、自分の境界線が曖昧なことです。",
                "苦しさの核は、相手の機嫌を先回りして背負ってしまうことです。",
                "問題は、心の中で何度も反論し続け、休めなくなることです。",
            ],
        }
    if category == "money":
        return {
            "scene": [
                f"{scene}のことを考えると、数字より先に不安が増える。",
                f"{scene}の話題で、先の見えなさに胸が重くなる。",
                f"{scene}で、焦って決めて後悔しそうになる。",
            ],
            "cause": [
                "原因は、不確かな未来を、今の心で全部背負ってしまうことです。",
                "苦しさの核は、足りない所だけを拡大して見てしまうことです。",
                "問題は、安心の基準が外側に寄りすぎることです。",
            ],
        }
    if category == "anger":
        return {
            "scene": [
                f"{scene}で、やる気が出ない自分を責めてしまう。",
                f"{scene}で、少しの失敗が頭から離れない。",
                f"{scene}で、焦りが焦りを呼んでしまう。",
            ],
            "cause": [
                "原因は、完璧を合格条件にしてしまうことです。",
                "苦しさの核は、心の中の採点が厳しすぎることです。",
                "問題は、休むことを悪いことだと決めつける癖です。",
            ],
        }
    # past/night default
    return {
        "scene": [
            f"{scene}で、{tag}がふくらみ、同じ考えが回り続ける。",
            f"{scene}で、体は休みたいのに頭だけが働き続ける。",
            f"{scene}で、静けさが逆に不安を呼んでしまう。",
        ],
        "cause": [
            "原因は、出来事そのものではなく、頭の中で何度も裁き直す癖です。",
            "苦しさの核は、未来や過去を先に抱え、今の体を置き去りにすることです。",
            "問題は、考えるほど安心できると思い込んでしまうことです。",
        ],
    }


def _parable_for(category: str, rng: random.Random) -> str:
    options: List[str]
    if category == "relationship":
        options = ["二本の矢", "筏"]
    elif category == "money":
        options = ["毒矢", "筏"]
    elif category == "anger":
        options = ["筏", "二本の矢"]
    else:
        options = ["二本の矢", "芥子の種", "筏"]
    return rng.choice(options)


def _parable_text(parable: str, *, tag: str) -> str:
    if parable == "毒矢":
        return (
            "昔、矢が刺さった人がいました。周りは矢を抜こうとしますが、本人は質問を始めます。"
            "誰が撃ったのか、どこで作られた矢なのか、なぜ自分なのか。"
            "答えが出るまで待っていたら、痛みは長引きます。"
            "ブッダが伝えたのは、今必要な手当てを先にすることです。"
            f"{tag}も同じで、全部を理解してから落ち着こうとすると遅れます。"
        )
    if parable == "筏":
        return (
            "川を渡るために筏を使った人がいます。渡り終えたあと、その人は筏を背負って歩き続けました。"
            "役に立ったものでも、目的が終われば置いていく。"
            "ブッダは、道具を道具として扱う知恵を大切にしました。"
            f"{tag}を解く方法も、手段にしがみつかず、今の自分に合う形へ軽く変えていいのです。"
        )
    if parable == "芥子の種":
        return (
            "昔、深い悲しみで立てなくなった人がいました。どうにか元に戻したくて、答えを探し回ります。"
            "ブッダは一つだけ頼みます。"
            "まだ誰も悲しみを経験していない家から、種をもらってきなさい、と。"
            "探しても見つかりません。誰の家にも、失った経験があるからです。"
            "そこで初めて、その人は自分だけが苦しいのではないと気づき、呼吸が戻りました。"
            f"{tag}も同じで、孤独だと思い込むほど重くなります。つながりを思い出すだけで少し軽くなります。"
        )
    # 二本の矢
    return (
        "ブッダは、二本の矢のたとえで苦しみを説明しました。"
        "一本目の矢は、避けにくい痛みです。体の不調や、出来事の衝撃です。"
        "二本目の矢は、心が自分に撃つ追加の矢です。なぜ自分だけなのか、もっとこうすべきだったのに、と責め続けます。"
        f"{tag}が強い夜は、この二本目が増えやすい。"
        "今日は、二本目を増やさないやり方に戻します。"
    )


def _ensure_pause(text: str, *, include_trailing: bool) -> str:
    out = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    out = out.replace("・", "と")  # validator guard (A-text forbids bullet dot)
    if include_trailing:
        if not out.endswith("\n---"):
            out = out + "\n\n---"
    return out.strip() + "\n"


def _extra_templates(category: str, chapter_num: int) -> List[str]:
    common = [
        "{tag}が出るのは、あなたが弱いからではありません。守ろうとしているものがあるからです。",
        "落ち着かないほど、答えを出そうとしてしまいます。でも今は、答えよりも休む順番です。",
        "頭の中の声は最悪を想定します。安全のために大げさに言うだけのことがあります。",
        "だから声の内容を全部信じなくていい。体に戻せば、声は自然に小さくなります。",
        "一度に変えようとしないでください。小さく戻すことが、続く近道です。",
        "うまくできない日があっても構いません。やり直せる形にしておけば、少しずつ整います。",
        "今のあなたを責めると、二本目の矢が増えます。責める代わりに、手当てに戻します。",
        "考えを減らす一番の方法は、考えない努力ではなく、今に戻る回数を増やすことです。",
        "心は空白があると埋めたくなります。夜に{tag}が出るのは自然です。",
        "必要なのは勇気ではなく順番です。順番が整うと、心は勝手に落ち着きます。",
        "ブッダの教えは、苦しみを増やさないための道具だと思ってください。",
        "{concept}を思い出すだけでも、心の緊張は少し緩みます。",
    ]
    by_category: Dict[str, List[str]] = {
        "relationship": [
            "相手を変えるより先に、自分が守る線を決めます。線があると、言葉が短くなります。",
            "長い説明は、相手にとっては反論の材料になります。短い一文のほうが心は守れます。",
            "距離を取ることは冷たいことではありません。心が摩耗しない距離は、優しさでもあります。",
            "相手の機嫌を背負うほど、こちらの呼吸が浅くなります。背負わない練習が必要です。",
            "言い返せない夜は、あなたが悪いのではなく、準備なしで戦場に立っているだけです。",
            "境界線は、怒るためではなく守るためにあります。守り方が分かると、争いは減ります。",
        ],
        "money": [
            "不安が強いときほど、数字を見るのが怖くなります。だから一つだけ確認します。",
            "安心は大金からだけ来るわけではありません。固定費を一つ整えるだけで呼吸が戻ります。",
            "未来は変えられますが、今夜の心は今夜しか休めません。休める順番を先にします。",
            "焦って決めると、安心のための選択が逆に不安を増やします。いったん落ち着いてからでいい。",
            "足りない所だけを見ると、心はずっと渇きます。今ある基盤にも目を向けます。",
            "小さな節度は、大きな安心につながります。派手な一発より、続く形が強いです。",
        ],
        "anger": [
            "完璧を合格条件にすると、心はずっと不合格になります。合格条件を下げるのではなく現実に合わせます。",
            "焦りは敵ではありません。守ろうとしている証拠です。だから扱い方だけ覚えます。",
            "小さな一歩が積み重なると、心は静かに強くなります。大きな決意は続かないことがあります。",
            "休むことは怠けではありません。回復は行動の一部です。回復があると続きます。",
            "自分を叱ると一瞬動けても、後で反動が来ます。静かに促す形のほうが長く持ちます。",
            "できない日の自分も含めて設計すると、習慣は折れにくくなります。",
        ],
        "past": [
            "{tag}は、過去を大切にしてきた証拠でもあります。だから乱暴に消そうとしないでください。",
            "思い出は、選び直せないからこそ痛みます。選び直す代わりに、今の一手を選びます。",
            "比較は、伸びたい気持ちの裏返しです。だから方向だけ整えれば、毒にはなりません。",
            "孤独だと思うほど、心は硬くなります。つながりを一つ思い出すだけで緩みます。",
            "後悔は、未来を守ろうとしている声でもあります。責め声ではなく案内として扱います。",
            "執着は悪ではありません。ただ握りしめる手が疲れているだけです。少し緩めます。",
        ],
        "night": [
            "静けさが怖い夜は、体が先に緊張しているだけかもしれません。体を先にほどきます。",
            "眠れないときは、眠ろうとするほど目が冴えます。眠るより休むに切り替えます。",
            "考えが止まらない夜は、心が働き者なだけです。働き方を変えれば味方になります。",
            "夜は結論を出す時間ではありません。心を静める時間です。結論は明日に回していい。",
            "暗い未来を想像するのは、備えようとしているからです。備えは明日にして、今夜は休みます。",
            "胸のざわつきは、危険ではなくサインです。サインを見たら、戻る場所に戻ります。",
        ],
    }
    by_chapter: Dict[int, List[str]] = {
        2: [
            "{scene}で、時計を見てしまう。まだ起きているのに、もう朝が怖くなる。そんな夜があります。",
            "{scene}で、ため息をついたあとに、また考えが始まる。体が休めない感じが残ります。",
            "{scene}で、小さな音や光に敏感になる。心が警戒している証拠です。",
        ],
        3: [
            "同じ場面を思い出すほど、脳はその場面を大事だと判断します。だから繰り返すほど強くなります。",
            "反省は短くなら役に立ちます。でも長くなると、ただの自責になって体を削ります。",
            "原因を一つに絞ると、手当てが決まります。手当てが決まると、安心が先に来ます。",
        ],
        6: [
            "三つの手順は、順番が大事です。体に戻す、置き場所を決める、小さな善い一手。これだけです。",
            "手順は完璧でなくていい。七割できたら合格にしてください。合格にすると続きます。",
            "今日の一手が小さいほど、明日もやれます。続くことが一番の回復です。",
        ],
        8: [
            "今夜は、全部を解決しなくていい。心が少し緩めば十分です。",
            "静かに終えます。息を一つ長く吐いて、体の重さを感じてください。",
            "あなたはもう十分に頑張っています。休むことも、あなたの仕事です。",
        ],
    }
    return common + by_category.get(category, []) + by_chapter.get(chapter_num, [])


def _chapter_text(st: Status, chapter_num: int, heading: str, *, target_len: int) -> str:
    title = str(st.metadata.get("title") or st.script_id or "")
    tag = str(st.metadata.get("main_tag") or _extract_title_tag(title) or "悩み").strip()
    benefit = str(st.metadata.get("benefit") or "").strip()
    scene = str(st.metadata.get("life_scene") or _channel_pick(st, "scenes", ["日常の場面"])).strip()
    concept = str(st.metadata.get("key_concept") or _channel_pick(st, "concepts", ["無常"])).strip()
    concept_expl = _CONCEPT_EXPLANATIONS.get(concept, "")
    category = _tag_category(tag) or ("relationship" if st.channel == "CH13" else "night")
    rng = _rng_for(st, salt=f"chapter:{chapter_num}")
    cat_parts = _paragraphs_for_category(category, tag=tag, scene=scene)

    opener = rng.choice(
        [
            f"夜になると、{tag}が強くなってしまうことがあります。",
            f"{tag}が顔を出すと、頭の中が静かになりません。",
            f"{tag}は、真面目な人ほど一人で抱え込みやすいものです。",
        ]
    )
    if category == "relationship":
        opener = rng.choice(
            [
                f"{tag}の悩みは、相手がいるぶん逃げ場がなく感じます。",
                f"{tag}は、我慢が続くほど言葉が出にくくなります。",
                f"{tag}は、正しさより疲れが残る時ほどつらいものです。",
            ]
        )
    if category == "money":
        opener = rng.choice(
            [
                f"{tag}の不安は、数字の前に心が先に揺れます。",
                f"{tag}は、先の見えなさがあるほど大きく感じます。",
                f"{tag}は、考えるほど怖くなる夜があります。",
            ]
        )

    paragraphs: List[str] = []

    if chapter_num == 1:
        paragraphs.append(opener)
        paragraphs.append(
            rng.choice(
                [
                    "この時間だけは、自分を責めないでください。責めるほど心は硬くなります。",
                    "苦しいと感じていること自体は、間違いではありません。今は休む合図です。",
                    "まずは、今日ここまで来た自分を否定しないでください。それだけで少し息が入ります。",
                ]
            )
        )
        paragraphs.append("今日は難しい言葉を使わず、苦しさを静める順番だけを整えます。")
        paragraphs.append("結論を先に言うと、止めようとするほど増える考えを、戻す場所を決めて減らします。")
        paragraphs.append("考えが出たら失敗ではありません。戻す練習の合図です。")
        if benefit:
            paragraphs.append(f"最後には、{benefit}という状態に近づくための三つの手順に落とします。")
    elif chapter_num == 2:
        paragraphs.append(rng.choice(cat_parts["scene"]))
        paragraphs.append(
            rng.choice(
                [
                    "胸のあたりがざわつき、呼吸が浅くなる。そんな感覚が残ることがあります。",
                    "目は閉じているのに、頭の中だけが明るく動き続ける。そんな夜があります。",
                    "体は休みたいのに、心だけが仕事を続ける。疲れの正体はそこにあります。",
                ]
            )
        )
        paragraphs.append(
            "その場では何とかこなしても、後から心だけが追いかけてきます。"
            "言い返せなかった言葉、選べなかった行動、見なかったことにした不安が、静かな時間に浮かびます。"
        )
        paragraphs.append(
            rng.choice(
                [
                    "そこで無理に前向きになろうとすると、余計に疲れます。",
                    "ここで何かを決めようとすると、心がさらに熱くなります。",
                    "眠ろうと頑張るほど、逆に目が冴えることがあります。",
                ]
            )
        )
        paragraphs.append("ここから先は、自分を責める話ではありません。仕組みを知って、今夜から軽くする話です。")
    elif chapter_num == 3:
        paragraphs.append(rng.choice(cat_parts["cause"]))
        paragraphs.append(
            "頭の中の言葉は、正しさを求めているようで、実は安心を探しています。"
            "でも安心は、考えを増やすほど遠ざかることがあります。"
        )
        paragraphs.append(
            "例えば、同じ場面を繰り返し思い出すほど、脳はそれを重要だと判断します。"
            "重要だと判断すると、さらに繰り返す。こうして輪が強くなります。"
        )
        paragraphs.append("だからこそ、原因を一つに絞り、手当ての順番を間違えないことが大切です。")
    elif chapter_num == 4:
        if concept_expl:
            paragraphs.append(f"ブッダは、{concept}という見方を大切にしました。{concept_expl}")
        else:
            paragraphs.append(f"ブッダは、{concept}という見方を大切にしました。")
        paragraphs.append(
            "心は、正しい答えを出したときに落ち着くのではなく、今の体に戻ったときに静まります。"
            "だから、考えを消すより、考えと距離を取る練習が効きます。"
        )
        paragraphs.append(
            "距離を取るというのは、冷たくなることではありません。"
            "苦しみを増やさないために、一歩引いて眺めるということです。"
        )
        paragraphs.append("ここで大事なのは、無理に前向きにならないことです。落ち着く形に戻すだけで十分です。")
    elif chapter_num == 5:
        parable = _parable_for(category, rng)
        paragraphs.append(_parable_text(parable, tag=tag))
        paragraphs.append(
            "一気に解決しなくていい。二本目を増やさないだけで、夜は少し短くなります。"
            "そのために、次は具体の手順に移ります。"
        )
    elif chapter_num == 6:
        paragraphs.append("ここからは、今日できる形に落とします。やることは三つです。")
        paragraphs.append(
            "まず一つ目は、今の体に戻すことです。息を少し長く吐いて、肩とあごの力を抜きます。"
            "頭の中の会話より先に、体の緊張をほどきます。"
        )
        paragraphs.append(
            "吐く息が少し長くなるだけで、心のスピードは落ちます。"
            "完璧にやろうとせず、少しだけ長く吐く。これで十分です。"
        )
        paragraphs.append(
            "二つ目は、考えを整理しようとせず、置き場所を決めることです。"
            "紙に一行だけ書く、明日やることは一つだけ決める。これで脳は安心します。"
        )
        paragraphs.append(
            "頭の中で片づけようとすると、夜は終わりません。"
            "外に一行出すだけで、脳は手放していいと判断します。"
        )
        paragraphs.append(
            "三つ目は、小さな善い行いに戻すことです。誰かに優しくする、部屋を一つだけ整える。"
            "大きな決意ではなく、小さな一手が心を静めます。"
        )
        paragraphs.append(
            "善い行いは、自分を許す方向に心を向けます。"
            "大げさなことではなく、小さくていい。小さいほど続きます。"
        )
        if benefit:
            paragraphs.append(f"この三つを順番にやると、{benefit}に向かう道が見えます。")
    elif chapter_num == 7:
        paragraphs.append("ここでよくある落とし穴があります。無理に消そうとすることです。")
        paragraphs.append(
            "考えが出た瞬間に追い払うと、余計に強く戻ってきます。"
            "出たこと自体を失敗にしない。戻す場所に戻るだけにします。"
        )
        paragraphs.append(
            "もう一つは、相手や過去を裁くことに時間を使いすぎることです。"
            "裁きは強い刺激なので、夜にやるほど眠りから遠ざかります。"
        )
        paragraphs.append(
            "夜は判断が荒くなりやすい時間です。"
            "結論を出すのではなく、静めることだけにします。"
        )
        paragraphs.append("夜は結論を出す時間ではなく、静める時間です。")
    else:
        paragraphs.append("今日の話を一言にすると、止めるより戻す、です。")
        paragraphs.append(
            f"{tag}が出ても大丈夫です。出たら、息と体に戻し、次に一手だけ整える。"
            "それだけで、夜は少しずつ短くなります。"
        )
        paragraphs.append(
            "今日できたのが一つでもあれば十分です。"
            "あなたの心は、今夜だけでも休んでいい。静かに終えましょう。"
        )

    # Fill to target length with deterministic, category-aware extra paragraphs.
    extra_pool = [
        t.format(tag=tag, scene=scene, concept=concept, benefit=benefit)
        for t in _extra_templates(category, chapter_num)
    ]
    rng.shuffle(extra_pool)
    while len("\n\n".join(paragraphs)) < max(0, target_len) and extra_pool:
        paragraphs.append(extra_pool.pop(0))

    joined = "\n\n".join(p.strip() for p in paragraphs if p and p.strip()).strip()
    include_trailing = chapter_num != 8
    return _ensure_pause(joined, include_trailing=include_trailing)


def generate_chapter_drafts_offline(
    base: Path,
    st: Status,
    chapters: List[Tuple[int, str]],
    *,
    per_chapter_target: int,
) -> List[str]:
    chapters_dir = base / "content" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    generated: List[str] = []
    for num, heading in chapters:
        out_path = chapters_dir / f"chapter_{num}.md"
        if out_path.exists():
            try:
                if out_path.stat().st_size > 0:
                    generated.append(str(out_path.relative_to(base)))
                    continue
            except Exception:
                pass
        text = _chapter_text(st, num, heading, target_len=per_chapter_target)
        out_path.write_text(text, encoding="utf-8")
        generated.append(str(out_path.relative_to(base)))
    return generated
