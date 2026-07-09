# dyn = Compiler.type_env.DYNAMIC
# must rewrite if moving from detyper to retyper
def update_type_context_pairs(dyn, types, type_contexts, reads, writes, removed_annotations):
    for anno in removed_annotations:
        for read in reads.get(anno) or []:
            types[read] = dyn
        for write in writes.get(anno) or []:
            type_contexts[write] = dyn
