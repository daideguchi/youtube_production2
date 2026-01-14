def append_closing(filepath):
    closing_text = "\n\nあなたの思考の、静かな伴走者として。『静寂の哲学』では、これからも物事の本質を探求していきます。よろしければ、チャンネル登録で、また次の思索の時間にお会いしましょう。\n"
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "伴走者として" in content:
        print(f"Closing already exists in {filepath}")
        return

    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(closing_text)
    print(f"Appended closing to {filepath}")

append_closing("scripts/CH02/058/content/assembled.md")
append_closing("scripts/CH02/059/content/assembled.md")
