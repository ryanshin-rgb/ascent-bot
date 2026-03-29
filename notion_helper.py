def get_ryan_todos(notion):
    try:
        results = notion.databases.query(
            database_id="d9c57c12ad29447985c44baab2a94f42",
            filter={"property": "완료여부", "checkbox": {"equals": False}}
        )
        items = []
        for r in results['results']:
            props = r.get('properties', {})
            for k, v in props.items():
                if v.get('type') == 'title':
                    titles = v.get('title', [])
                    if titles:
                        items.append(titles[0].get('plain_text', ''))
        return "\n".join(items) if items else "미완료 업무 없음"
    except Exception as e:
        return f"노션 조회 실패: {e}"
