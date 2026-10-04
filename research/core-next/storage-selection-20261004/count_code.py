"""按物理行统计非空、非注释、非docstring代码；保留多行SQL字符串。"""
import ast,io,json,pathlib,tokenize
ROOT=pathlib.Path(__file__).resolve().parents[3]
result={}
for name in ('storage','datasets','_json_chunks','threads','workspace','information_sql','memory'):
    path=ROOT/'src/society0/kernel'/f'{name}.py';source=path.read_text();docs=set()
    for node in ast.walk(ast.parse(source)):
        body=getattr(node,'body',None)
        if isinstance(body,list) and body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):
            docs.update(range(body[0].lineno,body[0].end_lineno+1))
    lines={line for token in tokenize.generate_tokens(io.StringIO(source).readline)
           if token.type not in (tokenize.COMMENT,tokenize.NL,tokenize.NEWLINE,tokenize.INDENT,tokenize.DEDENT,tokenize.ENDMARKER)
           for line in range(token.start[0],token.end[0]+1) if line not in docs}
    result[str(path.relative_to(ROOT))]={'physical_lines':len(source.splitlines()),'nonempty_noncomment_nondocstring_lines':len(lines)}
print(json.dumps(result,indent=2))
