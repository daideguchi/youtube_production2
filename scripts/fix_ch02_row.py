
import csv
import os

file_path = '/Users/dd/10_YouTube_Automation/factory_commentary/progress/channels/CH02.csv'

# Read all rows
with open(file_path, 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    rows = list(reader)

# Remove the last row (the incorrect one)
# Verify it's the one we just added (No. 31)
if rows[-1][0] == '31':
    print("Removing incorrect row 31...")
    rows.pop()
else:
    print(f"Last row is {rows[-1][0]}, expected 31. Not removing.")

# Construct the correct row
# 1-6: No...Title
part1 = ['31', 'CH02', '31', 'CH02-031', 'CH02-031', '【静かなる崩壊】なぜ「真面目な人」ほど、ある日突然心が折れるのか―サイレント・バーンアウト']
# 7-15: 9 empty columns
part2 = [''] * 9
# 16-18: Intent, Target, Content
part3 = [
    '「サイレント・バーンアウト」をテーマに、真面目な人が陥る静かな崩壊を描く。',
    '頭の中がいつも騒がしく、自己理解や人間関係に悩む30〜50代。哲学や心理学で心を整理し、静かな感情の置き場を探している人。',
    '【導入】静かな崩壊の兆候。\n【分析】なぜ真面目な人ほど折れるのか（高機能不安）。\n【哲学】ストア派の視点。\n【解決】燃え尽きる前の「静寂」の取り入れ方。'
]
# 19-36: 18 empty columns
part4 = [''] * 18
# 37-44: Tags and Descriptions (8 columns)
part5 = [
    '思考疲れ', '完璧主義', '深夜の書斎', 'ストア派 / 禅', '「燃え尽きる前に気づける」',
    '張り詰めた糸が音もなく切れる',
    '「頑張りすぎてしまうあなたへ。心が折れる前に、静かな休息を。」',
    '・サイレント・バーンアウトの兆候\n・高機能不安との付き合い方'
]

new_row = part1 + part2 + part3 + part4 + part5

print(f"New row length: {len(new_row)}")
print(f"Header length: {len(rows[0])}")

if len(new_row) != len(rows[0]):
    print("Error: Length mismatch!")
else:
    rows.append(new_row)
    with open(file_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print("Fixed row appended successfully.")
