function sourceTypeHasFullOutputArtifact(sourceType: string | undefined): boolean {
  return sourceType === 'agent';
}

export function buildAutoInsertedDependencyReference(
  sourceId: string,
  targetType: string,
  sourceType?: string,
): string {
  if (targetType !== 'agent' || !sourceTypeHasFullOutputArtifact(sourceType)) {
    return `\${${sourceId}.output}`;
  }

  return [
    `Dependency handoff from ${sourceId}:`,
    `- status: \${${sourceId}.status}`,
    `- artifact_path: \${${sourceId}.output_data.artifact_path}`,
    `- files_touched: \${${sourceId}.files}`,
    'Read artifact_path when exact upstream context is needed. The artifact is the full output; this prompt only carries the reference.',
  ].join('\n');
}
