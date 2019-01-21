/**
 * Containers that must fetch lists of data in pages should use this mixin. The container is
 * responsible for calling "transformDataPage" on the returned list, which will automatically
 * update the "hasNextPage" data field and remove the last document from the list if necessary.
 * The original Array passed to "transformDataPage" is not modified; a shallow copy is returned
 * in the case when it requires modification.
 */
export const pagingContainer = {
  props: {
    pageSize: {
      default: 30,
      type: Number,
    },
  },
  data: () => ({
    pageOffset: 0,
    hasNextPage: false,
  }),
  computed: {
    hasPrevPage() {
      return this.pageOffset > 0;
    },
    currentPage() {
      return this.pageOffset / this.pageSize;
    },
    pagingParams() {
      return {
        limit: this.pageSize + 1,
        offset: this.pageOffset,
      };
    },
  },
  methods: {
    fetchNextPage() {
      this.pageOffset += this.pageSize;
      return this.fetch();
    },
    fetchPrevPage() {
      this.pageOffset = Math.max(0, this.pageOffset - this.pageSize);
      return this.fetch();
    },
    fetchPage(n) {
      this.pageOffset = this.pageSize * n;
      return this.fetch();
    },
    transformDataPage(list) {
      this.hasNextPage = list.length > this.pageSize;
      if (this.hasNextPage) {
        return list.slice(0, -1);
      }
      return list;
    },
  },
};

/**
 * Any view component that needs to display human-readable data sizes should use this.
 */
export const sizeFormatter = {
  methods: {
    formatDataSize(size) {
      if (size < 1024) {
        return `${size} B`;
      }

      let i;
      for (i = 0; size >= 1024; i += 1) {
        size /= 1024;
      }

      return `${size.toFixed(2)}  ${['B', 'KB', 'MB', 'GB', 'TB'][Math.min(i, 4)]}`;
    },
  },
};
