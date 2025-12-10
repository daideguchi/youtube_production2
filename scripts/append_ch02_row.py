
import csv
import os

file_path = '/Users/dd/10_YouTube_Automation/factory_commentary/progress/channels/CH02.csv'

new_row = [
    '31', 'CH02', '31', 'CH02-031', 'CH02-031',
    '【静かなる崩壊】なぜ「真面目な人」ほど、ある日突然心が折れるのか―サイレント・バーンアウト',
    '', '', '', '', '', '', '', '', '',
    '「サイレント・バーンアウト」をテーマに、真面目な人が陥る静かな崩壊を描く。',
    '頭の中がいつも騒がしく、自己理解や人間関係に悩む30〜50代。哲学や心理学で心を整理し、静かな感情の置き場を探している人。',
    '【導入】静かな崩壊の兆候。\n【分析】なぜ真面目な人ほど折れるのか（高機能不安）。\n【哲学】ストア派の視点。\n【解決】燃え尽きる前の「静寂」の取り入れ方。',
    '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '',
    '思考疲れ', '完璧主義', '深夜の書斎', 'ストア派 / 禅', '「燃え尽きる前に気づける」',
    '張り詰めた糸が音もなく切れる',
    '「頑張りすぎてしまうあなたへ。心が折れる前に、静かな休息を。」',
    '・サイレント・バーンアウトの兆候\n・高機能不安との付き合い方'
]

# Verify column count matches header
with open(file_path, 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    header = next(reader)
    if len(new_row) != len(header):
        # Pad with empty strings if needed, or truncate
        # The header has 44 columns based on my count, let's check
        print(f"Header columns: {len(header)}")
        print(f"New row columns: {len(new_row)}")
        # Adjust new_row to match header length
        if len(new_row) < len(header):
            new_row += [''] * (len(header) - len(new_row))
        elif len(new_row) > len(header):
            new_row = new_row[:len(header)]

with open(file_path, 'a', encoding='utf-8', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(new_row)

print("Row appended successfully.")
