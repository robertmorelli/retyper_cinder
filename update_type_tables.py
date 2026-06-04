
# dyn = Compiler.type_env.DYNAMIC
# must rewrite if moving from detyper to retyper
def update_type_context_pairs(dyn, types, type_contexts, components, reads, writes, removed_annotations):
    for root in removed_annotations:
        component = (components.get(root) or set()) | set([root])
        for anno in component:
            for read in reads.get(anno) or []:
                types[read] = dyn
            for write in writes.get(anno) or []:
                type_contexts[write] = dyn
