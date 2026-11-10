// cases: D26 every Core/Social/Extras plugin that owns SQLite tables or migration factories exports migrations
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import ts from 'typescript';

const sourceRoots = ['core/src', 'social/src', 'extras/src'];

function parse(file) {
    return ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
}

function resolveModule(from, specifier) {
    if (!specifier.startsWith('.'))
        return undefined;
    const base = resolve(dirname(from), specifier);
    for (const candidate of [base.replace(/\.js$/, '.ts'), base + '.ts', base])
        if (existsSync(candidate) && statSync(candidate).isFile())
            return candidate;
    return undefined;
}

function exportedNames(file, seen = new Set()) {
    const names = new Set();
    if (seen.has(file))
        return names;
    seen.add(file);
    const source = parse(file);
    const visit = node => {
        if (ts.isVariableStatement(node) && node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) {
            for (const declaration of node.declarationList.declarations)
                if (ts.isIdentifier(declaration.name))
                    names.add(declaration.name.text);
        }
        else if (ts.isFunctionDeclaration(node) && node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) {
            if (node.name)
                names.add(node.name.text);
        }
        else if (ts.isClassDeclaration(node) && node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) {
            if (node.name)
                names.add(node.name.text);
        }
        else if (ts.isExportDeclaration(node) && !node.isTypeOnly) {
            if (node.exportClause && ts.isNamedExports(node.exportClause)) {
                for (const element of node.exportClause.elements)
                    if (!element.isTypeOnly)
                        names.add(element.name.text);
            }
            else if (node.exportClause && ts.isNamespaceExport(node.exportClause)) {
                names.add(node.exportClause.name.text);
            }
            else if (!node.exportClause && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
                const target = resolveModule(file, node.moduleSpecifier.text);
                if (target)
                    for (const name of exportedNames(target, seen))
                        names.add(name);
            }
        }
        ts.forEachChild(node, visit);
    };
    visit(source);
    return names;
}

function sourceFiles(directory) {
    const files = [];
    for (const name of readdirSync(directory)) {
        const path = join(directory, name);
        if (statSync(path).isDirectory())
            files.push(...sourceFiles(path));
        else if (path.endsWith('.ts'))
            files.push(path);
    }
    return files;
}

function stringLiterals(source) {
    const values = [];
    const visit = node => {
        if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
            values.push(node.text);
        else if (ts.isTemplateExpression(node)) {
            let text = node.head.text;
            for (const span of node.templateSpans)
                text += span.literal.text;
            values.push(text);
        }
        ts.forEachChild(node, visit);
    };
    visit(source);
    return values;
}

function migrationExports(files) {
    const names = new Set();
    for (const file of files) {
        const source = parse(file);
        const visit = node => {
            if (ts.isFunctionDeclaration(node) && node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword) && node.name && /Migration$/.test(node.name.text))
                names.add(node.name.text);
            else if (ts.isVariableStatement(node) && node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) {
                for (const declaration of node.declarationList.declarations)
                    if (ts.isIdentifier(declaration.name) && /Migrations?$/.test(declaration.name.text))
                        names.add(declaration.name.text);
            }
            else if (ts.isExportDeclaration(node) && !node.isTypeOnly && node.exportClause && ts.isNamedExports(node.exportClause)) {
                for (const element of node.exportClause.elements)
                    if (!element.isTypeOnly && /Migration$/.test(element.name.text))
                        names.add(element.name.text);
            }
            ts.forEachChild(node, visit);
        };
        visit(source);
    }
    return names;
}

function tableOwnership(files) {
    const text = files.map(file => readFileSync(file, 'utf8')).join('\n');
    if (text.includes('@society0/sqlite-native'))
        return new Set();
    const tables = new Set();
    for (const file of files)
        for (const value of stringLiterals(parse(file)))
            for (const match of value.matchAll(/CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+["`\[]?([A-Za-z_][\w]*)/gi))
                if (!match[1].startsWith('s0_'))
                    tables.add(match[1]);
    return tables;
}

function scanPlugins(root) {
    const violations = [];
    for (const sourceRoot of sourceRoots) {
        const base = join(root, sourceRoot);
        if (!existsSync(base))
            continue;
        for (const name of readdirSync(base).sort()) {
            const directory = join(base, name);
            if (!statSync(directory).isDirectory())
                continue;
            const entry = join(directory, 'index.ts');
            if (!existsSync(entry) || !exportedNames(entry).has('metadata'))
                continue;
            const files = sourceFiles(directory);
            if (exportedNames(entry).has('migrations'))
                continue;
            const migrations = [...migrationExports(files)].sort();
            const tables = [...tableOwnership(files)].sort();
            if (migrations.length || tables.length)
                violations.push({ plugin: name, migrations, tables });
        }
    }
    return violations;
}

test('D26 Core, Social and Extras plugins with their own SQLite schema export migrations', () => {
    const violations = scanPlugins(resolve('.'));
    assert.deepEqual(violations, [], JSON.stringify(violations));
});

test('D26 the schema scan catches a fake plugin that declares a migration without exporting migrations', () => {
    const root = mkdtempSync(join(tmpdir(), 'd26-plugin-scan-'));
    try {
        for (const [name, migration] of [['fake-bad', true], ['fake-good', false]]) {
            const directory = join(root, 'core/src', name);
            mkdirSync(directory, { recursive: true });
            writeFileSync(join(directory, 'index.ts'), [
                `export const metadata = { title: ${JSON.stringify(name)} };`,
                'export function fakeMigration(source: Readonly<Record<string, unknown>>) {',
                `    return { owner: ${JSON.stringify(name)}, from: 0, to: 1, after: [], source, statements: [{ sql: 'CREATE TABLE fake_rows(id TEXT PRIMARY KEY NOT NULL)' }] };`,
                '}',
                ...(migration ? [] : ['export const migrations = (source: Readonly<Record<string, unknown>>) => [fakeMigration(source)];'])
            ].join('\n'));
        }
        const violations = scanPlugins(root);
        assert.deepEqual(violations.map(item => item.plugin), ['fake-bad']);
        assert.deepEqual(violations[0].migrations, ['fakeMigration']);
        assert.deepEqual(violations[0].tables, ['fake_rows']);
    }
    finally {
        rmSync(root, { recursive: true, force: true });
    }
});
